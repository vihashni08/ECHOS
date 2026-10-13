"""Watchdog: restarts the runtime process when it exits or stops reporting; emulator resumes after a restart. Throw-away child processes, no network."""
import sys
import time

from sqlalchemy import select

from echos.orchestrator import state as S
from echos.orchestrator.watchdog import Watchdog

FAST = (0.05, 0.05, 0.05)


def restarts(eng):
    with eng.begin() as c:
        return c.execute(select(S.restart_log).where(S.restart_log.c.agent_id == "runtime")).fetchall()


def test_an_exited_runtime_is_restarted_and_logged(tmp_path):
    w = Watchdog([sys.executable, "-c", "import sys; sys.exit(3)"], tmp_path / "w.db", stale_s=5, grace_s=60, poll_s=0.05, backoff_s=FAST)
    w.check_once()                                   # spawns
    time.sleep(0.3)
    assert w.check_once() == "exited_3" and w.restarts == 1
    time.sleep(0.3)
    assert w.check_once() == "exited_3" and w.restarts == 2
    rows = restarts(w.eng)
    assert [r.reason for r in rows] == ["exited_3", "exited_3"] and all(r.restarted_ts >= r.detected_ts for r in rows)
    w.proc.kill()


def test_a_runtime_that_stops_reporting_is_killed_and_restarted(tmp_path):
    w = Watchdog([sys.executable, "-c", "import time; time.sleep(60)"], tmp_path / "w.db", stale_s=0.3, grace_s=0.2, poll_s=0.05, backoff_s=FAST)
    w.check_once()
    first = w.proc.pid
    time.sleep(0.6)
    assert w.check_once() == "stale_liveness" and w.proc.pid != first
    assert [r.reason for r in restarts(w.eng)] == ["stale_liveness"]
    w.proc.kill()


def test_a_healthy_runtime_is_left_alone(tmp_path):
    import json
    w = Watchdog([sys.executable, "-c", "import time; time.sleep(60)"], tmp_path / "w.db", stale_s=5, grace_s=0.1, poll_s=0.05, backoff_s=FAST)
    w.check_once()
    time.sleep(0.3)
    with w.eng.begin() as c:
        S.upsert(c, S.sim_state, ["key"], key="updated", value=json.dumps(time.time()))
    assert w.check_once() is None and w.restarts == 0
    w.proc.kill()


def test_emulator_resumes_paused_at_its_position_after_a_restart(tmp_path):
    from echos.live.emulator import Emulator
    eng = S.get_engine(tmp_path / "e.db")
    e1 = Emulator(eng, cfg={"speed": 6 * 3600})
    e1.load(start_day="2019-05-13", n_days=2, n_patients=1)
    e1.status = "playing"
    e1.tick(wall=0.0)
    e1.tick(wall=3.0)
    assert e1.cursor > 0
    e2 = Emulator(eng)                               # the runtime was restarted
    assert e2.resume() and e2.status == "paused" and e2.cursor == e1.cursor and e2.sim_now == e1.sim_now and e2.patients == e1.patients
    e2.status = "playing"
    e2.tick(wall=10.0)
    e2.tick(wall=11.0)
    assert e2.cursor > e1.cursor                     # continues, and the already-written events are not written twice
    with eng.begin() as c:
        n = c.execute(select(S.event_log.c.event_id)).fetchall()
    assert len(n) == e2.cursor
