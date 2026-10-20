import os
import signal
import threading
import time

import pytest
from sqlalchemy import func, select

from echos.config import load_config
from echos.orchestrator import state as S
from echos.orchestrator.pipeline import build_pipeline, process_case
from echos.orchestrator.runtime import AgentWorker
from echos.orchestrator.supervisor import Supervisor
from echos.orchestrator.triage import triage
from echos.sim.replay import replay, train_replay_fall_model

CFG = load_config()["triage"]
FP = CFG["fall_prob"]


def R(score=0.0, **ev):
    return {"score": score, "flag": False, "evidence": ev}


def vit(sys=120, dia=80, hr=70, score=0.0):
    return {"available": True, "probability": score, "flag": score >= 0.278, "current_vs_baseline":
            {"sys": {"current": sys}, "dia": {"current": dia}, "hr": {"current": hr}}}


def health(v=None, degraded=False):
    return {"vitals": v or vit(), "degraded": degraded}


def qual(**ev):
    base = {"quality_checked": True, "days_since_last_reading": 0, "invalid_values": [], "has_vitals": True}
    return R(0.0, **{**base, **ev})


def res(v=None, fall=None, quality=None):
    return {"quality": quality or qual(), "health": health(v), "fall": fall or R(0.0, post_fall_inactive=False)}


# ------------------------------------------------------------------ triage branches
@pytest.mark.parametrize("r,status,gap", [
    (res(fall=R(FP + 0.05, post_fall_inactive=True)), "URGENT", False),    # at/above the operating threshold, confirmed by inactivity
    (res(fall=R(FP + 0.05, post_fall_inactive=False)), "URGENT" if CFG["fall_prob_high"] is not None and FP + 0.05 >= CFG["fall_prob_high"] else "MONITOR", False),
    (res(fall=R(FP - 0.1, post_fall_inactive=True)), "NORMAL", False),     # below the operating threshold
    (res(v=vit(sys=190)), "URGENT", False),                             # critical vitals
    (res(v=vit(hr=35)), "URGENT", False),
    (res(v=vit(score=0.7)), "MONITOR", False),
    (res(), "NORMAL", False),
])
def test_triage_branches(r, status, gap):
    d = triage(r, 0, CFG)
    assert d.status.value == status and d.data_gap == gap
    assert (not d.reasons) == (status == "NORMAL")


@pytest.mark.parametrize("r,dsv", [
    (res(quality=qual(days_since_last_reading=CFG["max_vitals_gap_days"] + 1)), 0),  # no vitals for too long
    (res(quality=qual(quality_checked=False)), 0),       # Agent 0 failed: pass-through, unchecked
    ({**res(), "quality": None}, 0),                      # Agent 0 output missing
    ({**res(), "health": None}, 0),                       # health monitoring output missing
    ({**res(), "health": {**health(), "degraded": True}}, 0),  # fallback: monitoring degraded
])
def test_data_gap_never_normal(r, dsv):
    d = triage(r, dsv, CFG)
    assert d.data_gap and d.status.value == "MONITOR" and any(x.startswith("DATA_GAP:") for x in d.reasons)


def test_urgent_beats_gap():
    d = triage(res(fall=R(0.99, post_fall_inactive=True), quality=qual(days_since_last_reading=CFG["max_vitals_gap_days"] + 3)), 0, CFG)
    assert d.status.value == "URGENT" and d.data_gap


# ------------------------------------------------------------------ graph resume
def _seed(eng):
    with eng.begin() as c:
        for k in ("day", "sensor"):
            eid = c.execute(S.event_log.insert().values(patient_id="p1", day="2019-05-05", ts="t", kind=k, payload="{}")).inserted_primary_key[0]
            agents = ["quality", "health_monitoring"] if k == "day" else ["fall"]
            for a in agents:
                r = {"quality": qual(), "health_monitoring": health(),
                     "fall": R(0.97, post_fall_inactive=True)}[a]
                S.insert_ignore(c, S.agent_output, event_id=eid, agent_id=a, patient_id="p1", day="2019-05-05", result=S.j(r), done_ts=1.0)


def test_interrupted_run_resumes_without_duplicate_alert(tmp_path):
    eng = S.get_engine(tmp_path / "t.db")
    _seed(eng)
    boom = {"n": 0}

    def fault(node):
        if node == "immediate_alert" and boom["n"] == 0:
            boom["n"] += 1
            raise RuntimeError("crash after alert write")

    g = build_pipeline(eng, fault=fault, weekly_pdf=False)
    with pytest.raises(RuntimeError):
        process_case(g, "p1", "2019-05-05")
    # new "process": fresh graph object on the same checkpoint file resumes the unfinished thread
    g2 = build_pipeline(eng, weekly_pdf=False)
    assert process_case(g2, "p1", "2019-05-05") is True
    assert process_case(g2, "p1", "2019-05-05") is False       # finished threads are not re-run
    assert g2.dispatcher.flush(10) and g.dispatcher.flush(10)   # mail goes out on a background thread
    with eng.begin() as c:
        assert c.execute(select(func.count()).select_from(S.alerts)).scalar() == 1
        assert c.execute(select(S.alerts.c.status)).scalar() == "SENT"      # derived from the outbox (DryRun mailer, send_real=false)
        assert c.execute(select(func.count()).select_from(S.outbox)).scalar() == 2   # caregiver + clinician, once each despite the crash/resume
        st = c.execute(select(S.case_state)).first()
    assert st.status == "URGENT"


# ------------------------------------------------------------------ runtime / supervisor
@pytest.fixture(scope="module")
def fall_model():
    return train_replay_fall_model()


def _counts(eng):
    with eng.begin() as c:
        ev = c.execute(select(func.count()).select_from(S.event_log).where(S.event_log.c.kind == "sensor")).scalar()
        out = c.execute(select(func.count()).select_from(S.agent_output).where(S.agent_output.c.agent_id == "fall")).scalar()
    return ev, out


def _wait(cond, t=60):
    t0 = time.time()
    while time.time() - t0 < t:
        if cond():
            return True
        time.sleep(0.2)
    return False


def test_worker_is_idempotent(tmp_path, fall_model):
    eng = S.get_engine(tmp_path / "t.db")
    replay(eng, 1, max_days=3)
    w = AgentWorker("fall", tmp_path / "t.db", hb_s=0.5, ck_s=0.5, poll_s=0.1, fall_model=fall_model)
    th = threading.Thread(target=w.run)
    th.start()
    assert _wait(lambda: _counts(eng)[0] == _counts(eng)[1])
    w.stop.set(); th.join()
    n1 = _counts(eng)
    with eng.begin() as c:  # lose the checkpoint -> full re-read of the stream
        c.execute(S.agent_checkpoint.delete())
    w2 = AgentWorker("fall", tmp_path / "t.db", hb_s=0.5, ck_s=0.5, poll_s=0.1, fall_model=fall_model)
    th = threading.Thread(target=w2.run); th.start(); time.sleep(2); w2.stop.set(); th.join()
    assert _counts(eng) == n1 and w2.n_done == n1[0]


def _sup(tmp_path, fall_model, **wa):
    return Supervisor(tmp_path / "t.db", agents=("fall",), hb_s=1.0, ck_s=1.0, stall_s=4, poll_s=0.3, missed=3,
                      startup_grace_s=40, fall_model=fall_model, worker_args={"fall": wa})


def test_supervisor_restarts_killed_agent_and_resumes(tmp_path, fall_model):
    eng = S.get_engine(tmp_path / "t.db")
    sup = _sup(tmp_path, fall_model, delay=0.1)
    sup.start_all()
    threading.Thread(target=sup.run, daemon=True).start()
    try:
        threading.Thread(target=lambda: replay(eng, 1, max_days=12, rate_eps=15), daemon=True).start()
        assert _wait(lambda: _counts(eng)[1] >= 8)
        with eng.begin() as c:
            pid = c.execute(select(S.agent_heartbeat.c.pid)).scalar()
        os.kill(pid, signal.SIGKILL)
        assert _wait(lambda: S.get_engine(tmp_path / "t.db").connect().execute(select(func.count()).select_from(S.restart_log)).scalar() >= 1)
        assert _wait(lambda: _counts(eng)[0] >= 25 and _counts(eng)[0] == _counts(eng)[1], 90)
    finally:
        sup.stop_all()
    with eng.begin() as c:
        assert c.execute(select(S.restart_log.c.reason)).scalar() == "missed_heartbeats"


def test_supervisor_detects_stalled_last_event_id(tmp_path, fall_model):
    eng = S.get_engine(tmp_path / "t.db")
    replay(eng, 1, max_days=6)
    sup = _sup(tmp_path, fall_model, **{"hang-after": 3})
    sup.start_all()
    threading.Thread(target=sup.run, daemon=True).start()
    try:
        assert _wait(lambda: S.get_engine(tmp_path / "t.db").connect().execute(select(S.restart_log.c.reason)).first() is not None, 90)
    finally:
        sup.stop_all()
    with eng.begin() as c:
        assert c.execute(select(S.restart_log.c.reason)).scalar() == "stalled_last_event_id"
