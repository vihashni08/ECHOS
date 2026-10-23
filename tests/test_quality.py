"""Sensor-silence / data-quality rules: the pure functions, and the Patient Monitor on a synthetic outage (real agents, temporary database)."""
import json

from sqlalchemy import select

from echos.monitor import agitation as A, quality as Q
from echos.orchestrator import state as S
from tests.test_team_v2 import _insert, _run, eng, models_dir  # noqa: F401  (fixtures)


def test_silent_window_needs_history_and_a_usual_that_is_active():
    assert Q.silent_window(0, [60, 55, 70], None) == (False, None)                 # fewer than 4 earlier windows: nothing to compare with
    assert Q.silent_window(0, [60, 55, 70, 65], None)[0] is True                    # active person, total silence
    assert Q.silent_window(0, [3, 0, 5, 2, 4], None)[0] is False                    # a person who is usually quiet at that time is not flagged
    assert Q.silent_window(7, [60, 55, 70, 65], None)[0] is False                   # some events at all: not silent


def test_day_assessment_and_vitals_gap():
    w = lambda silent: {"slot": 12, "silent": silent, "usual": 60.0}  # noqa: E731
    assert Q.assess_day("2019-05-10", [w(True), w(False), w(False)], None)["device_problem"] is False        # one silent window is only a note
    a = Q.assess_day("2019-05-10", [w(True), w(True), w(False)], None)
    assert a["device_problem"] and a["silent_windows"] == 2 and any("check the sensors" in f for f in a["flags"])
    stale = Q.assess_day("2019-05-10", [], 9)
    assert stale["device_problem"] and "no BP / pulse reading for 9 days" in stale["flags"]
    assert not Q.assess_day("2019-05-10", [], 7)["device_problem"]                   # exactly the limit is still fine
    assert Q.vitals_gap_days("2019-05-10", "2019-05-01") == 9 and Q.vitals_gap_days("2019-05-10", None) is None


def _outage_events():
    ev = []
    for i in range(12):
        ds = f"2019-05-{i + 1:02d}"
        for p in ("p1", "p2"):
            silent = p == "p1" and i >= 5                        # p1's sensors go dead from day 6; p2 stays normal
            wins = [{"w": f"{ds}T{h:02d}:00:00", "slot": h, **{c: 0.0 for c in A.RAW}, "total": 0.0 if silent else 60.0} for h in (8, 12, 18)]
            vit = {"sys": 130, "dia": 80, "hr": 72} if i < 2 else {}      # nobody has a reading after day 2: stale from day 10
            ev.append(dict(patient_id=p, day=ds, ts=f"{ds}T18:00:00", kind="day", payload={"vitals": vit, "windows": wins}))
        for p in ("p1", "p2"):
            ev.append(dict(patient_id=p, day=ds, ts=f"{ds}T20:00:00", kind="outcome", payload={"vitals_event": False, "agitation_windows": []}))
        ev.append(dict(patient_id="", day=ds, ts=f"{ds}T23:59:59", kind="day_close", payload={"day": ds, "patients": ["p1", "p2"]}))
    return ev


def test_monitor_flags_a_synthetic_outage_and_nothing_else(eng, models_dir):  # noqa: F811
    _insert(eng, _outage_events())
    _run(eng, models_dir, k=1)
    with eng.begin() as c:
        res = {(r.patient_id, r.day): json.loads(r.result) for r in c.execute(select(S.agent_output.c.patient_id, S.agent_output.c.day, S.agent_output.c.result))}
        gap = {(r.patient_id, r.day): r.data_gap for r in c.execute(select(S.case_state.c.patient_id, S.case_state.c.day, S.case_state.c.data_gap))}
    assert not res[("p1", "2019-05-05")]["quality"]["device_problem"]                       # before the outage
    q = res[("p1", "2019-05-06")]["quality"]
    assert q["device_problem"] and q["silent_windows"] == 3 and gap[("p1", "2019-05-06")]  # first dead day
    assert all(not res[("p2", f"2019-05-{i:02d}")]["quality"]["silent_windows"] for i in range(1, 13))   # a normal person is never flagged for silence
    assert res[("p2", "2019-05-11")]["quality"]["vitals_gap_days"] == 9 and res[("p2", "2019-05-11")]["quality"]["device_problem"]   # stale vitals
    assert not res[("p2", "2019-05-04")]["quality"]["device_problem"]
    with eng.begin() as c:
        req = [json.loads(r.payload) for r in c.execute(select(S.agent_message.c.payload).where((S.agent_message.c.topic == "review_request")
                                                                                         & (S.agent_message.c.patient_id == "p1") & (S.agent_message.c.day == "2019-05-06")))]
    assert req and any(r.startswith("data check:") for r in req[0]["reasons"])             # the review request carries the note in plain words
    assert res[("p1", "2019-05-06")]["agitation_max"] is not None                           # scoring is untouched: the flag only adds a note
