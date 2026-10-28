"""ECHOS v2 agents on a temporary database (synthetic events, fake models directory): message flow, URGENT path, allocation, feedback,
idempotency after a restart, and the v2 API endpoints."""
import json

import joblib
import numpy as np
import pandas as pd
import pytest
from sqlalchemy import func, select

from echos.alerts.dispatcher import AlertDispatcher
from echos.alerts.mailer import DryRunMailer
from echos.monitor import agitation as A, store
from echos.orchestrator import state as S
from echos.team import bus
from echos.team.care_agent import CareAgent
from echos.team.patient_monitor import PatientMonitor, PatientMonitorAgent
from echos.team.evidence_agent import EvidenceAgent
from echos.team.review_coordinator import ReviewCoordinatorAgent


class ConstModel:
    """Stands in for the agitation model: probability grows with the 'total' events of a window."""
    def __init__(self):
        self.features = A.feature_sets()["full"]

    def predict_proba(self, df):
        return np.clip(df["total"].values / 100.0, 0, 1)

    def top_reasons(self, row, k=3):
        return [f"total {row.get('total', 0):.0f}"]


@pytest.fixture()
def models_dir(tmp_path, monkeypatch):
    d = tmp_path / "v2"
    d.mkdir()
    nig = {v: {"mu0": m, "kappa0": 2.0, "alpha0": 3.0, "beta0": 2 * s ** 2} for v, m, s in (("sys", 135, 12), ("dia", 75, 8), ("hr", 70, 8))}
    meta = {"vitals_fold_of": {}, "agitation_fold_of": {}, "agitation_features": [],
            "folds": {"all": {"nig": nig, "beta": {"a0": 1.0, "b0": 4.0}}}, "base_rates": {"v_bar": 0.3, "a_bar": 0.05}}
    (d / "meta.json").write_text(json.dumps(meta))
    joblib.dump(ConstModel(), d / "agitation_all.joblib")
    store.clear_cache()
    monkeypatch.setattr(store, "DIR", d)
    yield d
    store.clear_cache()


def _events(days=6, patients=("p1", "p2", "p3")):
    ev = []
    for i in range(days):
        ds = f"2019-05-{i + 1:02d}"
        for p in patients:
            sys_ = 195 if (p == "p1" and i == 2) else 130 + (i % 3)
            wins = [{"w": f"{ds}T{h:02d}:00:00", "slot": h, **{c: 0.0 for c in A.RAW}, "total": 60.0 if (p == "p2" and h == 12) else 10.0}
                    for h in (8, 12, 18)]
            ev.append(dict(patient_id=p, day=ds, ts=f"{ds}T18:00:00", kind="day", payload={"vitals": {"sys": sys_, "dia": 80, "hr": 72}, "windows": wins}))
        for p in patients:
            ev.append(dict(patient_id=p, day=ds, ts=f"{ds}T20:00:00", kind="outcome",
                           payload={"vitals_event": p == "p3", "agitation_windows": [f"{ds}T12:00:00"] if p == "p2" else []}))
        ev.append(dict(patient_id="", day=ds, ts=f"{ds}T23:59:59", kind="day_close", payload={"day": ds, "patients": list(patients)}))
    return ev


def _run(eng, models_dir, k=1):
    disp = AlertDispatcher(eng, DryRunMailer(), to={"caregiver": "cg@example.test", "clinician": "cl@example.test"}, threaded=False)
    pm = PatientMonitorAgent(eng, models_dir=models_dir)
    rc = ReviewCoordinatorAgent(eng, models_dir=models_dir, cfg={"k_per_day": k, "fairness_days": 3, "fair_share": 0.5, "watch_lift": 1.5, "wait_s": 0})
    ca = CareAgent(eng, disp)
    ev = EvidenceAgent(eng, retriever=None, llm=None)          # template mode: no index, no network
    for _ in range(200):
        if not (pm.step() + ca.step() + rc.step() + ev.step()):
            break
    disp.run_due()
    return pm, rc, ca


@pytest.fixture()
def eng(tmp_path):
    e = S.get_engine(tmp_path / "t.db")
    return e


def _insert(eng, ev):
    with eng.begin() as c:
        for e in ev:
            c.execute(S.event_log.insert().values(patient_id=e["patient_id"], day=e["day"], ts=e["ts"], kind=e["kind"], payload=S.j(e["payload"])))


def test_full_flow(eng, models_dir):
    _insert(eng, _events())
    _run(eng, models_dir, k=1)
    with eng.begin() as c:
        alerts = c.execute(select(S.alerts.c.alert_id)).fetchall()
        reviews = c.execute(select(S.review_list)).fetchall()
        topics = dict(c.execute(select(S.agent_message.c.topic, func.count()).group_by(S.agent_message.c.topic)).fetchall())
        new = c.execute(select(func.count()).select_from(S.agent_message).where((S.agent_message.c.status == "NEW") & (S.agent_message.c.recipient != "all"))).scalar()
        st = dict(c.execute(select(S.case_state.c.day, S.case_state.c.status).where(S.case_state.c.patient_id == "p1")).fetchall())
    assert [a.alert_id.split("|")[:3] for a in alerts] == [["p1", "2019-05-03", "critical_vitals"]]     # one red-flag reading -> one alert
    assert st["2019-05-03"] == "URGENT"                                                                 # the Coordinator never downgrades it
    assert len(reviews) == 6 and len({r.day for r in reviews}) == 6                                      # K=1 per day, every day allocated
    assert topics["review_request"] == 18 and topics["feedback"] == 18 and topics["assign"] == 6 and topics["urgent"] == 1
    assert new == 0                                                                                       # every message consumed


def test_feedback_is_learned_and_drives_priority(eng, models_dir):
    _insert(eng, _events())
    _run(eng, models_dir, k=1)
    with eng.begin() as c:
        states = {r.patient_id: PatientMonitor.from_json(r.state) for r in c.execute(select(S.patient_agent))}
        last = c.execute(select(S.review_list).where(S.review_list.c.day == "2019-05-06")).fetchall()
    assert states["p3"].k == 6 and states["p3"].n == 6          # p3: a confirmed vitals event every day
    assert states["p1"].k == 0
    assert len(states["p2"].episode_windows) == 6               # p2: a confirmed agitation window every day
    assert {r.patient_id for r in last} <= {"p2", "p3", "p1"}


def test_fairness_floor_reaches_quiet_patient(eng, models_dir):
    _insert(eng, _events(days=8))
    _run(eng, models_dir, k=2)
    with eng.begin() as c:
        picked = {r.patient_id for r in c.execute(select(S.review_list))}
        reasons = {r.reason for r in c.execute(select(S.review_list))}
    assert picked == {"p1", "p2", "p3"} and "fairness" in reasons


def test_restart_is_idempotent(eng, models_dir):
    _insert(eng, _events())
    _run(eng, models_dir, k=1)
    with eng.begin() as c:                                       # forget the cursors (a crash before the checkpoint): re-reading must change nothing
        c.execute(S.agent_checkpoint.delete())
        before = (c.execute(select(func.count()).select_from(S.alerts)).scalar(), c.execute(select(func.count()).select_from(S.agent_output)).scalar())
        k_before = {r.patient_id: PatientMonitor.from_json(r.state).k for r in c.execute(select(S.patient_agent))}
    pm = PatientMonitorAgent(eng, models_dir=models_dir)
    pm.run_until_idle()
    with eng.begin() as c:
        after = (c.execute(select(func.count()).select_from(S.alerts)).scalar(), c.execute(select(func.count()).select_from(S.agent_output)).scalar())
        k_after = {r.patient_id: PatientMonitor.from_json(r.state).k for r in c.execute(select(S.patient_agent))}
    assert before == after and k_before == k_after


def test_next_day_waits_for_previous_outcome(eng, models_dir):
    ev = _events(days=2)
    _insert(eng, [e for e in ev if not (e["kind"] == "outcome" and e["day"] == "2019-05-01")] )
    pm = PatientMonitorAgent(eng, models_dir=models_dir)
    pm.max_wait_s = 3600
    pm.run_until_idle()
    with eng.begin() as c:                                       # day 1 has no outcome event -> nothing to wait for, day 2 is scored
        n = c.execute(select(func.count()).select_from(S.agent_output)).scalar()
    assert n == 6


def test_v2_api(eng, models_dir, tmp_path):
    from fastapi.testclient import TestClient
    from echos.api.app import create_app
    _insert(eng, _events())
    _run(eng, models_dir, k=1)
    from echos.api.services import Ctx
    app = create_app(Ctx(eng=eng))
    cl = TestClient(app)
    assert [p["patient_id"] for p in cl.get("/api/patients").json()["items"]] == ["p1", "p2", "p3"]     # no empty id from day_close events
    r = cl.get("/api/v2/review").json()
    assert r["day"] == "2019-05-06" and len(r["items"]) == 1 and r["items"][0]["outcome"] in ("CONFIRMED", "NO_EVENT")
    dv = cl.get("/api/v2/day").json()                                                       # the daily review: every patient, one day, all four agents
    assert dv["day"] == "2019-05-06" and dv["allocated"] and {p["patient_id"] for p in dv["patients"]} == {"p1", "p2", "p3"}
    picked = [p for p in dv["patients"] if p["rank"] is not None]
    assert len(picked) == 1 and dv["patients"][0] is not None and dv["patients"][0]["rank"] == 1     # picks come first
    assert all(p["priority"] is None or p["priority"] <= picked[0]["priority"] + 1e-9 for p in dv["patients"] if p["pick_reason"] != "fairness")
    d3 = cl.get("/api/v2/day", params={"day": "2019-05-03"}).json()
    p1 = next(p for p in d3["patients"] if p["patient_id"] == "p1")
    assert p1["urgent"] and any(c["kind"] == "urgent" for c in p1["evidence"]) and p1["vitals"]
    t = cl.get("/api/v2/patients/p1/monitor").json()
    assert t[0]["day"] == "2019-05-06" and any(d["urgent"] for d in t)
    assert t[0]["rate_ci95"][0] <= t[0]["rate"] <= t[0]["rate_ci95"][1]
    o = cl.post("/api/v2/reviews/outcome", json={"patient_id": "p1", "day": "2019-05-06", "vitals_event": True}).json()
    assert o["status"] == "QUEUED"
    assert bus.poll(eng, "care", ["clinician_outcome"])[0]["payload"]["vitals_event"] is True
    ev_cards = cl.get("/api/v2/evidence", params={"patient_id": "p1"}).json()                  # one urgent card, template mode (no index in tests)
    assert [c["kind"] for c in ev_cards].count("urgent") == 1 and all(c["source"] == "template" for c in ev_cards)
    tr = cl.get("/api/v2/trace/p1/2019-05-03").json()
    assert tr["status"] == "URGENT" and tr["monitor"]["safety"] and any(m["topic"] == "urgent" for m in tr["messages"]) and tr["evidence"]
    s = cl.get("/api/v2/summary").json()
    assert s["urgent_alerts"] == 1 and s["reviews"] == 6
    ev = cl.get("/api/events", params={"last_event_id": 0, "max_seconds": 1.5}).text      # the SSE stream replays the feed (alerts, review lists, messages)
    assert ": connected" in ev and "event: review_list" in ev and "event: alert" in ev


def test_replay_controller_resumes_after_a_restart(tmp_path):
    from echos.sim.controller import SimController
    eng = S.get_engine(tmp_path / "r.db")
    ev = _events(days=3)
    c1 = SimController(eng, builder=lambda: ev)
    c1.handle("play", {})
    for _ in range(10):
        c1.emit_next()
    c2 = SimController(eng, builder=lambda: ev)          # the runtime was restarted by the watchdog
    assert c2.resume() == 10 and c2.status == "paused" and c2.cursor == 10
    c2.handle("play", {})
    while c2.emit_next():
        pass
    with eng.begin() as c:
        assert c.execute(select(func.count()).select_from(S.event_log)).scalar() == len(ev)     # nothing written twice


def test_components_doc_is_up_to_date():
    from scripts.docgen.make_components_doc import ROOT as R, render
    assert (R / "docs" / "COMPONENTS.md").read_text() == render(), "run: PYTHONPATH=. python scripts/docgen/make_components_doc.py"
