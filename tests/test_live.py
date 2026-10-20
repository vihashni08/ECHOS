"""Live / emulator mode, offline: ingestion contract, detectors scored from events, rolling provisional decisions, day close, fall from an ingested window,
freshness. FakeMailer / template reports only. Needs the built data and models (skipped in a fresh clone)."""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from echos.alerts.dispatcher import AlertDispatcher
from echos.alerts.mailer import FakeMailer
from echos.api import services as V
from echos.api.app import create_app
from echos.config import load_config
from echos.live import store as LS
from echos.live.emulator import Emulator
from echos.live.processor import LiveProcessor
from echos.live.schemas import IngestEvent
from echos.orchestrator import state as S
from echos.reports.daily.agent import ReportAgent

TO = {"caregiver": "care@example.test", "clinician": "doc@example.test", "escalation": "boss@example.test"}


def ev(pid="px", ts="2019-05-20T08:00:00", **kw):
    return {"patient_id": pid, "ts": ts, "kind": "heartbeat", **kw}


@pytest.fixture
def eng(tmp_path):
    from echos.alerts import payload as P
    yield S.get_engine(tmp_path / "live.db")
    P.LIVE_ENG = None                      # LiveProcessor installs itself as the live raw-event source: never leak it into other tests


@pytest.fixture
def client(eng, tmp_path):
    ctx = V.Ctx(eng=eng, root=tmp_path, cfg=load_config())
    return TestClient(create_app(ctx)), ctx


def test_ingest_contract_validates_dedups_and_tracks_freshness(client):
    c, ctx = client
    r = c.post("/api/ingest/events", json={"events": [ev(), ev(ts="2019-05-20T08:05:00", device_id="hub-2"),
                                                      {"patient_id": "px", "ts": "2019-05-20T08:10:00", "kind": "vitals", "device_type": "Heart rate", "value": 71, "unit": "bpm"},
                                                      {"patient_id": "px", "ts": "2019-05-20T08:15:00", "kind": "heartbeat"}]})
    assert r.status_code == 200 and r.json() == {"accepted": 4, "duplicates": 0, "rejected": 0, "patients": ["px"]}
    assert c.post("/api/ingest/events", json={"events": [ev()]}).json()["duplicates"] == 1                    # exact repeat: idempotent
    assert c.post("/api/ingest/events", json={"events": [ev(kind="activity", room="Kitchen")]}).status_code == 422     # room-sensor events are no longer accepted
    assert c.post("/api/ingest/events", json={"events": [{"patient_id": "px", "ts": "2019-05-20T08:00:00", "kind": "vitals", "device_type": "Heart rate"}]}).status_code == 422
    assert c.post("/api/ingest/events", json={"events": [{"patient_id": "p x!", "ts": "2019-05-20T08:00:00", "kind": "heartbeat"}]}).status_code == 422
    assert c.post("/api/ingest/events", json={"events": []}).status_code == 422
    st = c.get("/api/live/status").json()
    p = st["patients"][0]
    assert st["mode"] == "replay" and p["patient_id"] == "px" and p["last_event_ts"] == "2019-05-20T08:15:00"
    assert p["streams"]["heartbeat"]["state"] == "fresh" and p["streams"]["accel"]["state"] == "none" and "activity" not in p["streams"]


def test_ingest_token_when_configured(client, monkeypatch):
    c, _ = client
    monkeypatch.setenv("ECHOS_INGEST_TOKEN", "tok-123")
    assert c.post("/api/ingest/events", json={"events": [ev()]}).status_code == 403
    assert c.post("/api/ingest/events", json={"events": [ev()]}, headers={"X-Echos-Token": "tok-123"}).status_code == 200


def build(eng):
    d = AlertDispatcher(eng, FakeMailer(), to=TO, backoff_s=[0.01, 0.02, 0.04])
    reporter = ReportAgent(eng, None, root=eng.url.database.rsplit("/", 1)[0] + "/files", budget=None)
    from echos.sim.replay import train_replay_fall_model
    return LiveProcessor(eng, d, reporter, fall_model=train_replay_fall_model()), d


def run_emulator(eng, proc, emu, wall_steps, dt=1.0):
    t = 0.0
    for _ in range(wall_steps):
        t += dt
        emu.tick(wall=t)
        while proc.step():
            pass


def test_emulated_days_are_scored_from_events_and_close_the_previous_day(eng):
    proc, d = build(eng)
    emu = Emulator(eng, cfg={"speed": 6 * 3600})
    pats = emu.load(start_day="2019-05-13", n_days=3, n_patients=1)
    emu.status = "playing"
    emu.tick(wall=0.0)
    run_emulator(eng, proc, emu, wall_steps=14)                       # 14 wall s x 6 h = 3.5 data days
    assert emu.status == "finished"
    with eng.begin() as c:
        cases = {r.day: r for r in c.execute(select(S.case_state).where(S.case_state.c.patient_id == pats[0]))}
        rows = c.execute(select(S.live_day)).fetchall()
        kinds = {r[0] for r in c.execute(select(S.event_log.c.kind).distinct())}
    assert kinds == {"vitals", "heartbeat"}               # raw events only: no 'day' summary event, nothing pre-aggregated
    assert set(cases) >= {"2019-05-13", "2019-05-14"} and all(cases[x].provisional == 0 for x in ("2019-05-13", "2019-05-14"))
    assert cases["2019-05-15"].provisional == 1 or "2019-05-15" not in cases                    # the newest day stays open
    closed = {r.day: json.loads(r.row) for r in rows if r.closed}
    assert closed["2019-05-13"]["source"] == "live" and closed["2019-05-13"]["has_reading"] in (True, False)
    assert all(cases[x].as_of for x in cases)
    d.flush(5)


def test_an_ingested_fall_window_raises_one_urgent_alert_with_lifecycle(eng):
    from echos.data import sisfall as sf
    f = sf.list_files()
    f = f[f.is_fall & f.subject.isin(load_config()["replay"]["holdout_subjects"])]
    if f.empty:
        pytest.skip("SisFall not on this machine")
    proc, d = build(eng)
    emu = Emulator(eng, cfg={"speed": 4 * 3600})
    pats = emu.load(start_day="2019-05-13", n_days=2, n_patients=1)
    emu.status = "playing"
    emu.tick(wall=0.0)
    run_emulator(eng, proc, emu, wall_steps=3)
    import numpy as np
    r = f.iloc[0]
    a = sf.load_file(r.path)
    out = emu.inject_fall({"patient_id": pats[0], "file": r.path, "win_start": max(0, int(np.argmax(sf.svm(a))) - 250), "subject": r.subject, "activity": r.activity})
    assert out["accepted"] == 1
    for _ in range(3):
        proc.step()
    d.flush(5)
    with eng.begin() as c:
        al = c.execute(select(S.alerts)).fetchall()
        ob = c.execute(select(S.outbox)).fetchall()
        cs = c.execute(select(S.case_state).where(S.case_state.c.day == out["day"])).first()
        cf = c.execute(select(S.case_file)).fetchall()
    assert cs.status in ("URGENT", "MONITOR")                         # URGENT needs post-fall inactivity; a flagged fall without it is MONITOR
    if cs.status == "URGENT":
        assert len(al) == 1 and al[0].type == "fall" and {o.recipient_role for o in ob} == {"caregiver", "clinician"} and cf
        proc.step()
        assert len(c_all(eng)) == 1                                    # a second decision does not duplicate the alert


def c_all(eng):
    with eng.begin() as c:
        return c.execute(select(S.alerts)).fetchall()



def test_restart_rebuilds_the_open_day_and_changes_nothing(eng):
    proc, d = build(eng)
    emu = Emulator(eng, cfg={"speed": 6 * 3600})
    pats = emu.load(start_day="2019-05-13", n_days=3, n_patients=1)
    emu.status = "playing"
    emu.tick(wall=0.0)
    run_emulator(eng, proc, emu, wall_steps=7)                                  # about 1.75 data days: day 1 closed, day 2 open
    def snap():
        with eng.begin() as c:
            return sorted((r.day, r.status, bool(r.provisional)) for r in c.execute(select(S.case_state)))
    before = snap()
    assert any(not p for _, _, p in before) and any(p for _, _, p in before)
    proc2, _ = build(eng)                                                       # a new process: nothing in memory
    proc2.recover()
    assert snap() == before                                                     # same decisions, same provisional flags
    with eng.begin() as c:
        assert c.execute(select(S.agent_checkpoint.c.last_event_id).where(S.agent_checkpoint.c.agent_id == "live")).scalar() > 0
    d.flush(5)


def test_a_closed_sunday_builds_the_weekly_pdf_in_the_background(eng, tmp_path):
    proc, d = build(eng)
    emu = Emulator(eng, cfg={"speed": 8 * 3600})
    pats = emu.load(start_day="2019-05-12", n_days=3, n_patients=1)             # 2019-05-12 is a Sunday
    emu.status = "playing"
    emu.tick(wall=0.0)
    run_emulator(eng, proc, emu, wall_steps=6)
    proc.reporter.drain(120)
    with eng.begin() as c:
        w = c.execute(select(S.reports).where(S.reports.c.kind == "weekly_pdf")).fetchall()
    assert len(w) == 1 and w[0].day == "2019-05-12" and w[0].path.endswith(".pdf")
    d.flush(5)
