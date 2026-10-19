"""Fault scenarios for the LIVE path (fakes only: FakeMailer, no LLM, no network). Each scenario returns facts; tests/test_live_faults.py asserts them and
`python scripts/evaluation/live_fault_check.py` writes reports/metrics/live_faults.json (with measured software latency). One machine, one run: small samples.
Usage: PYTHONPATH=. python scripts/evaluation/live_fault_check.py"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

os.environ.setdefault("ECHOS_REPORT_LLM", "off"); os.environ.setdefault("ECHOS_PUBMED", "off"); os.environ.setdefault("ECHOS_DRY_RUN", "1")
import numpy as np  # noqa: E402
from sqlalchemy import select  # noqa: E402

from echos.alerts.dispatcher import AlertDispatcher  # noqa: E402
from echos.alerts.mailer import FakeMailer  # noqa: E402
from echos.config import ROOT, load_config  # noqa: E402
from echos.live import store as LS  # noqa: E402
from echos.live.emulator import Emulator  # noqa: E402
from echos.live.processor import LiveProcessor  # noqa: E402
from echos.live.schemas import IngestEvent  # noqa: E402
from echos.orchestrator import state as S  # noqa: E402
from echos.reports.agent import ReportAgent  # noqa: E402
from echos.team.coordinator import CareCoordinator  # noqa: E402

TO = {"caregiver": "care@example.test", "clinician": "doc@example.test", "escalation": "boss@example.test"}


def world(mailer=None, **dkw):
    from echos.sim.replay import train_replay_fall_model
    tmp = Path(tempfile.mkdtemp())
    eng = S.get_engine(tmp / "f.db")
    d = AlertDispatcher(eng, mailer or FakeMailer(), to=TO, backoff_s=[0.01, 0.02, 0.04], **dkw)
    proc = LiveProcessor(eng, d, ReportAgent(eng, None, root=tmp / "files", budget=None), fall_model=train_replay_fall_model())
    return eng, d, proc


def stream(eng, proc, emu, wall_steps, dt=1.0, t0=0.0):
    t = t0
    for _ in range(wall_steps):
        t += dt
        emu.tick(wall=t)
        while proc.step():
            pass
    return t


def fall_window():
    from echos.data import sisfall as sf
    f = sf.list_files()
    f = f[f.is_fall & (f.subject == "SA03") & (f.activity == "F02")]
    if f.empty:
        return None
    r = f.iloc[0]
    a = sf.load_file(r.path)
    return {"file": r.path, "win_start": max(0, int(np.argmax(sf.svm(a))) - 250), "subject": r.subject, "activity": r.activity}


def start(eng, speed, n_patients=1, n_days=3, day="2019-05-13"):
    emu = Emulator(eng, cfg={"speed": speed})
    pats = emu.load(start_day=day, n_days=n_days, n_patients=n_patients)
    emu.status = "playing"
    emu.tick(wall=0.0)
    return emu, pats


def s_duplicates_and_late_events():
    eng, d, proc = world()
    emu, pats = start(eng, 6 * 3600, n_days=3)
    stream(eng, proc, emu, 8)
    with eng.begin() as c:
        evs = c.execute(select(S.event_log).where(S.event_log.c.kind.in_(["activity", "vitals"])).limit(200)).fetchall()
        before = sorted((r.day, r.status, r.provisional) for r in c.execute(select(S.case_state)))
        n_before = c.execute(select(S.event_log.c.event_id)).fetchall()
    redo = [IngestEvent(patient_id=r.patient_id, device_id=json.loads(r.payload)["device_id"], ts=r.ts, kind=r.kind, source="emulator",
                        **({"room": json.loads(r.payload)["room"]} if r.kind == "activity" else {"device_type": json.loads(r.payload)["device_type"], "value": json.loads(r.payload)["value"]})) for r in evs]
    res = LS.write_events(eng, redo)
    late = LS.write_events(eng, [IngestEvent(patient_id=pats[0], ts="2019-05-13T03:00:00", kind="activity", room="Kitchen", device_id="late-1", source="device")])   # a day that already closed
    while proc.step():
        pass
    with eng.begin() as c:
        after = sorted((r.day, r.status, r.provisional) for r in c.execute(select(S.case_state)))
        n_after = c.execute(select(S.event_log.c.event_id)).fetchall()
    d.flush(5)
    return {"resent": len(redo), "duplicates_detected": res["duplicates"], "late_event_stored": late["accepted"], "decisions_changed": before != after, "extra_rows": len(n_after) - len(n_before) - late["accepted"]}


def s_vitals_detector_down():
    from echos.agents.health import vitals as vm
    eng, d, proc = world()
    orig = vm.predict_live
    vm.predict_live = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("model file missing"))
    try:
        emu, pats = start(eng, 6 * 3600, n_days=3)
        stream(eng, proc, emu, 8)
    finally:
        vm.predict_live = orig
    with eng.begin() as c:
        cs = c.execute(select(S.case_state)).fetchall()
    d.flush(5)
    return {"days": len(cs), "normal_days": sum(r.status == "NORMAL" for r in cs), "all_marked_data_gap": all(r.data_gap for r in cs), "reasons_mention_degraded": all("degraded" in r.reasons for r in cs)}


def s_fall_window_not_analysed():
    from echos.agents import fall
    w = fall_window()
    if w is None:
        return {"skipped": "no SisFall"}
    eng, d, proc = world()
    emu, pats = start(eng, 4 * 3600, n_days=2)
    stream(eng, proc, emu, 2)
    orig = fall.predict_window
    fall.predict_window = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        emu.inject_fall({"patient_id": pats[0], **w})
        while proc.step():
            pass
    finally:
        fall.predict_window = orig
    with eng.begin() as c:
        cs = c.execute(select(S.case_state).where(S.case_state.c.day == "2019-05-13")).first()
    return {"status": cs.status, "data_gap": bool(cs.data_gap), "alert_count": len(_alerts(eng)), "processor_alive": True}


def _alerts(eng):
    with eng.begin() as c:
        return c.execute(select(S.alerts)).fetchall()


def s_smtp_down_everywhere():
    w = fall_window()
    if w is None:
        return {"skipped": "no SisFall"}
    eng, d, proc = world(FakeMailer(fail_for=tuple(TO.values())), retry_window_s=0.4)
    coord = CareCoordinator(eng, d, cfg={"ack_timeout_s": {"FALL": 300}, "stale_after_s": 3600})
    emu, pats = start(eng, 4 * 3600, n_days=2)
    stream(eng, proc, emu, 2)
    emu.inject_fall({"patient_id": pats[0], **w})
    while proc.step():
        pass
    time.sleep(1.5)
    d.flush(10)
    for _ in range(3):
        coord.step()
        time.sleep(0.2)
    d.flush(10)
    with eng.begin() as c:
        ob = c.execute(select(S.outbox)).fetchall()
        st = c.execute(select(S.alert_state)).fetchall()
    return {"alerts": len(_alerts(eng)), "outbox_rows": len(ob), "statuses": sorted({r.status for r in ob}), "none_looks_delivered": all(r.status != "SENT" for r in ob),
            "alert_state": [r.state for r in st], "escalation_row_visible": any(r.recipient_role == "escalation" for r in ob)}


def s_report_agent_down():
    w = fall_window()
    if w is None:
        return {"skipped": "no SisFall"}
    eng, d, proc = world()
    proc.reporter.daily = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("report store unavailable"))
    emu, pats = start(eng, 4 * 3600, n_days=2)
    stream(eng, proc, emu, 2)
    emu.inject_fall({"patient_id": pats[0], **w})
    while proc.step():
        pass
    d.flush(5)
    with eng.begin() as c:
        cs = c.execute(select(S.case_state).where(S.case_state.c.day == "2019-05-13")).first()
    return {"status": cs.status, "alerts": len(_alerts(eng)), "alert_sent_despite_report_failure": len(_alerts(eng)) == 1 or cs.status != "URGENT"}


def s_burst_and_latency():
    w = fall_window()
    if w is None:
        return {"skipped": "no SisFall"}
    eng, d, proc = world()
    emu, pats = start(eng, 24 * 3600, n_patients=4, n_days=2)
    t0 = time.time()
    t = stream(eng, proc, emu, 3)
    n_events = emu.cursor
    lat = []
    for _ in range(3):
        t_inj = time.time()
        r = emu.inject_fall({"patient_id": pats[len(lat) % len(pats)], **w})
        while proc.step():
            pass
        with eng.begin() as c:
            row = c.execute(select(S.alerts.c.time).order_by(S.alerts.c.time.desc())).first()
        lat.append(round(row[0] - t_inj, 3) if row and row[0] >= t_inj else None)
        emu.sim_now = emu.sim_now + np.timedelta64(13, "h")
    d.flush(5)
    return {"patients": len(pats), "events_processed": n_events, "events_per_s": round(n_events / max(time.time() - t0, 1e-6)), "inject_to_alert_queued_s": lat}


SCENARIOS = [s_duplicates_and_late_events, s_vitals_detector_down, s_fall_window_not_analysed, s_smtp_down_everywhere, s_report_agent_down, s_burst_and_latency]


def main():
    out = {}
    for f in SCENARIOS:
        t0 = time.time()
        out[f.__name__[2:]] = {**f(), "seconds": round(time.time() - t0, 1)}
        print(f.__name__, json.dumps(out[f.__name__[2:]]))
    out["notes"] = ["fakes for mail / LLM; one machine; the emulator replays real TIHM events, falls are SisFall windows",
                    "inject_to_alert_queued_s is SOFTWARE latency inside one process (event written -> alert row), not a delivery time"]
    (ROOT / "reports/metrics/live_faults.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
