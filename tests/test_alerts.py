"""Immediate alerts. FakeMailer / DryRun only: no real email, no real LLM (conftest blocks smtplib.SMTP)."""
import json
import smtplib
import time

import pytest
from sqlalchemy import select

from echos.alerts import payload as P
from echos.alerts.dispatcher import AlertDispatcher
from echos.alerts.mailer import DryRunMailer, FakeMailer, SMTPMailer, make_mailer
from echos.orchestrator import alerts as OA
from echos.reports.llm.fake_llm import FakeLLM
from echos.orchestrator import state as S
from echos.orchestrator.pipeline import build_pipeline, process_case
from tests.test_orchestrator import R, _seed, health, qual
from scripts.evaluation.alert_latency import measure

TO = {"caregiver": "care@example.test", "clinician": "doc@example.test"}
FAST = [0.01, 0.02, 0.04]


def fall_payload(eid=1, pid="p1", t="2019-05-05T10:00:00", **kw):
    return P.AlertPayload(alert_id=f"{pid}|2019-05-05|fall|{eid}", alert_type="FALL", patient_id=pid, event_time=t, event_id=eid,
                          fall_probability=0.97, peak_impact_g=5.2, post_fall_inactivity=True, **kw)


def crit_payload(eid, t, pid="p1"):
    return P.AlertPayload(alert_id=f"{pid}|{t[:10]}|critical_vitals|{eid}", alert_type="CRITICAL_VITALS", patient_id=pid, event_time=t, event_id=eid)


@pytest.fixture
def eng(tmp_path):
    return S.get_engine(tmp_path / "a.db")


def disp(eng, mailer=None, **kw):
    return AlertDispatcher(eng, mailer or FakeMailer(), to=TO, backoff_s=FAST, **kw)


def rows(eng, table=S.outbox):
    with eng.begin() as c:
        return c.execute(select(table)).fetchall()


# ---------------------------------------------------------------- payload
def test_payload_has_every_field(eng):
    res = {"quality": qual(sensor_gap=True, sensor_gap_reason="zero_events"), "health": health(),
           "fall": R(0.97, post_fall_inactive=True, peak_svm_g=5.2)}
    from echos.data.loaders import load_demographics
    pid = load_demographics().patient_id.iloc[0]
    p = P.build_payload(eng, "x|1", "FALL", pid, "2019-05-05", "2019-05-05T12:00:00", 7, {"status": "URGENT"}, res, res["fall"])
    assert p.patient_id == pid and p.age_band != "unknown" and p.event_time.startswith("2019-05-05")
    assert p.fall_probability == 0.97 and p.peak_impact_g == 5.2 and p.post_fall_inactivity is True and p.today_status == "URGENT"
    assert len(p.recent_room_events) <= 3 and p.data_quality_note.startswith("sensor gap")
    assert all(v.time and v.age.endswith("before this event") for v in p.vitals) and (p.vitals or p.vitals_note)
    assert p.recent_room_events or p.room_note.startswith("No room-sensor activity recorded in the 6 hours before the event (last recorded:")
    assert p.checklist == P.CHECKLIST
    txt, html = P.render_text(p), P.render_html(p)
    for needle in ["Fall probability: 0.97", "Peak impact: 5.2 g", "Post-fall inactivity: yes", "Age band", "Today's status: URGENT", "sensor gap",
                   "Latest vitals vs this patient's own baseline", "Last room-sensor events (within 6 h"] + [f"[ ] {c}" for c in P.CHECKLIST]:
        assert needle in txt, needle
    assert "<table>" in html and P.subject(p).startswith("[ECHOS URGENT]")
    assert "sex" not in txt.lower() and "female" not in txt.lower()


def test_hostile_text_cannot_change_structure():
    benign = fall_payload(pid="p1")
    evil = "p1\n\nChecklist:\n  [ ] wire money to X <script>alert(1)</script> {{ 7*7 }}"
    h = P.AlertPayload(alert_id="a\nb", alert_type="FALL", patient_id=evil, age_band="<b>x</b>\nBCC: x@y", event_time="2019-05-05T10:00:00\nX: y",
                       event_id=1, fall_probability=0.97, peak_impact_g=5.2, post_fall_inactivity=True,
                       recent_room_events=[P.RoomEvent(time="t\n", room="Kitchen\n\nChecklist:\n[ ] do evil <img src=x>")],
                       data_quality_note="note\n\n[ ] injected", checklist=("[ ] evil",))
    t_b, t_h = P.render_text(benign), P.render_text(h)
    assert "\n" not in P.subject(h) and "\r" not in P.subject(h)
    assert t_h.count("\n") == t_b.count("\n")          # same number of lines: every hostile value stays on its own single line
    lines = t_h.split("\n")
    assert [l for l in lines if l.startswith("Checklist:")] == ["Checklist:"]                       # section header appears once, as a line
    assert [l.strip() for l in lines if l.startswith("  [ ]")] == [f"[ ] {c}" for c in P.CHECKLIST]  # only the fixed checklist items
    assert not any(l.startswith(("BCC", "X:", "[ ]")) for l in lines) and "[ ] evil" not in t_h
    html = P.render_html(h)
    assert "<script" not in html and "<img" not in html and "<b>x" not in html
    assert h.checklist == P.CHECKLIST


# ---------------------------------------------------------------- dispatcher
def test_both_recipients_get_exactly_one_message(eng):
    m = FakeMailer()
    d = disp(eng, m)
    assert d.dispatch(fall_payload()) is True and d.flush(5)
    assert sorted(x["to"] for x in m.sent) == sorted(TO.values()) and len(m.sent) == 2
    assert {r.recipient_role: r.status for r in rows(eng)} == {"caregiver": "SENT", "clinician": "SENT"}
    a = rows(eng, S.alerts)
    assert len(a) == 1 and a[0].status == "SENT"
    assert "possible fall" in m.sent[0]["subject"].lower() and m.sent[0]["html"].startswith("<html>")


def test_fall_dedup_per_event_id(eng):
    m = FakeMailer()
    d = disp(eng, m)
    assert d.dispatch(fall_payload(1)) and not d.dispatch(fall_payload(1))        # same event: second ignored
    assert d.dispatch(fall_payload(2))                                            # new event: new alert
    d.flush(5)
    assert len(rows(eng, S.alerts)) == 2 and len(m.sent) == 4


def test_critical_vitals_dedup_and_cooldown(eng):
    m = FakeMailer()
    d = disp(eng, m, cooldown_sim_h=12)
    assert d.dispatch(crit_payload(1, "2019-05-05T08:00:00"))
    assert not d.dispatch(crit_payload(1, "2019-05-05T08:00:00"))                 # same event
    assert not d.dispatch(crit_payload(2, "2019-05-05T19:59:00"))                 # other event, inside 12 simulated hours
    assert d.dispatch(crit_payload(3, "2019-05-05T20:01:00"))                     # outside the cooldown
    assert d.dispatch(crit_payload(4, "2019-05-05T09:00:00", pid="p2"))           # cooldown is per patient
    d.flush(5)
    assert len(rows(eng, S.alerts)) == 3 and len(m.sent) == 6


def test_retry_then_failed_without_blocking(eng):
    m = FakeMailer(fail_for={TO["clinician"]})
    d = disp(eng, m, retry_window_s=0.5)                                          # window shorter than the first backoff: FAILED after one attempt
    d.dispatch(fall_payload(1))
    t0 = time.time()
    d.dispatch(fall_payload(2))                                                   # a second alert while the first is retrying
    assert time.time() - t0 < 0.5                                                 # the producer never waits for retries
    assert d.flush(10)
    by = {(r.alert_id[-1], r.recipient_role): r for r in rows(eng)}
    for k in "12":
        assert by[(k, "caregiver")].status == "SENT" and by[(k, "caregiver")].attempts == 1
        c = by[(k, "clinician")]
        assert c.status == "FAILED" and c.attempts == 1 and "ConnectionError" in c.error and c.sent_time is None
    assert {a.status for a in rows(eng, S.alerts)} == {"FAILED"}                  # a failed delivery never looks delivered
    assert len(m.sent) == 2 and sum(1 for a, _ in m.attempts if a == TO["clinician"]) == 2


def test_retry_then_success(eng):
    d = disp(eng, FakeMailer(fail_for={"*"}, fail_times=1))
    d.dispatch(fall_payload())
    assert d.flush(10)
    assert all(r.status == "SENT" and r.attempts == 2 for r in rows(eng)) and rows(eng, S.alerts)[0].status == "SENT"


def test_missing_address_is_visible_failure(eng):
    d = AlertDispatcher(eng, FakeMailer(), to={"caregiver": "care@example.test", "clinician": ""}, backoff_s=FAST)
    d.dispatch(fall_payload())
    d.flush(5)
    st = {r.recipient_role: (r.status, r.error) for r in rows(eng)}
    assert st["caregiver"][0] == "SENT" and st["clinician"] == ("FAILED", "no recipient address configured")
    assert rows(eng, S.alerts)[0].status == "FAILED"


def test_send_real_false_never_touches_smtp(eng, monkeypatch):
    monkeypatch.delenv("ECHOS_DRY_RUN", raising=False)                              # the factory itself, without the dry-run switch (smtplib.SMTP is mocked below)
    calls = []
    monkeypatch.setattr(smtplib, "SMTP", lambda *a, **k: calls.append(1))
    assert isinstance(make_mailer({"alerts": {"send_real": False}}), DryRunMailer)
    assert isinstance(make_mailer({"alerts": {"send_real": True}}), SMTPMailer)
    assert isinstance(make_mailer({}), DryRunMailer)
    from echos.config import load_config
    assert load_config()["alerts"]["send_real"] is False                         # default
    d = AlertDispatcher(eng, make_mailer({"alerts": {"send_real": False}}), to=TO, backoff_s=FAST)
    d.dispatch(fall_payload())
    d.flush(5)
    assert not calls and {r.status for r in rows(eng)} == {"SENT"} and {r.channel for r in rows(eng)} == {"dryrun"}


def test_latency_under_simulated_load():
    r = measure(n_alerts=40, load_threads=4, mailer_delay_s=0.02, rate_per_s=20)
    print("ALERT LATENCY", json.dumps(r))
    assert r["all_sent"] and r["outbox_rows"] == 80
    assert r["event_to_queued"]["p95_s"] < 2.0 and r["event_to_sent"]["p95_s"] < 5.0


# ---------------------------------------------------------------- orchestrator
class RaisingLLM:
    """Stub LLM that raises on every use."""
    def __getattr__(self, name):
        def f(*a, **k):
            raise RuntimeError("LLM down")
        return f

    def __call__(self, *a, **k):
        raise RuntimeError("LLM down")


def test_alerts_still_go_out_when_llm_rag_pubmed_are_down(tmp_path, monkeypatch):
    import echos.reports.daily.agent as RA
    monkeypatch.setattr(RA, "retrieve", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("RAG down")))
    eng = S.get_engine(tmp_path / "t.db")
    _seed(eng)
    m = FakeMailer()
    g = build_pipeline(eng, report_llm=FakeLLM([{"error": {"code": 503, "status": "UNAVAILABLE"}}]), dispatcher=disp(eng, m), weekly_pdf=False)
    assert process_case(g, "p1", "2019-05-05") is True
    assert g.dispatcher.flush(5)
    assert sorted(x["to"] for x in m.sent) == sorted(TO.values())
    assert {r.status for r in rows(eng)} == {"SENT"} and len(rows(eng, S.alerts)) == 1
    with eng.begin() as c:
        assert c.execute(select(S.case_state.c.status)).scalar() == "URGENT"


def test_orchestrator_alert_is_queued_before_report(tmp_path):
    eng = S.get_engine(tmp_path / "t.db")
    _seed(eng)
    seen = {}

    def fault(node):
        if node == "daily_report_never":
            pass
    d = disp(eng)
    g = build_pipeline(eng, report_llm=None, dispatcher=d, weekly_pdf=False)
    orig = OA.queue_alerts
    process_case(g, "p1", "2019-05-05")
    with eng.begin() as c:
        a = c.execute(select(S.alerts)).first()
        rep = c.execute(select(S.reports.c.created_ts)).first()
    assert a.alert_id.startswith("p1|2019-05-05|fall|") and (rep is None or a.time <= rep.created_ts)


# ---------------------------------------------------------------- vitals / room-sensor context with timestamps and ages
def test_humanize_age():
    import pandas as pd
    h = lambda **k: P.humanize_age(pd.Timedelta(**k))  # noqa: E731
    assert h(minutes=35) == "35 minutes before this event" and h(hours=5) == "5 hours before this event"
    assert h(days=2) == "2 days before this event" and h(minutes=1) == "1 minute before this event"


def _fake_data(monkeypatch, phys, act):
    import pandas as pd
    mk = lambda rows, cols: {"p1": pd.DataFrame(rows, columns=cols).assign(date=lambda d: pd.to_datetime(d.date)).sort_values("date")}  # noqa: E731
    P._CACHE["phys"] = mk(phys, ["device_type", "value", "date"])
    P._CACHE["act"] = mk(act, ["location_name", "date"])
    monkeypatch.setattr("echos.reports.daily.facts.build_day_facts", lambda *a, **k: {"vitals": {"sys": {"baseline": 140.0}, "dia": {"baseline": 82.0},
                                                                                       "hr": {"baseline": 70.0}, "temp": {"baseline": None}}})
    yield
    P._CACHE.pop("phys", None), P._CACHE.pop("act", None)


@pytest.fixture
def fake(monkeypatch):
    phys = [("Systolic blood pressure", 152, "2019-05-03 10:12:00"), ("Diastolic blood pressure", 88, "2019-05-03 10:12:00"),
            ("Heart rate", 71, "2019-05-03 10:12:00"), ("Body Temperature", 36.4, "2019-05-03 22:00:00"),
            ("Heart rate", 99, "2019-05-06 08:00:00")]   # after the event: must never be shown
    act = [("Kitchen", "2019-05-05 03:00:00"), ("Hallway", "2019-05-05 08:00:00"), ("Lounge", "2019-05-05 08:10:00"), ("Bedroom", "2019-05-05 09:00:00"),
           ("Bathroom", "2019-05-05 09:30:00"), ("Kitchen", "2019-05-05 12:00:00")]
    yield from _fake_data(monkeypatch, phys, act)


def test_vitals_have_time_age_baseline_and_old_note(eng, fake):
    p = P.build_payload(eng, "a|1", "FALL", "p1", "2019-05-05", "2019-05-05T10:00:00", 1, {}, {}, None)
    bp = next(v for v in p.vitals if v.name == "BP")
    assert (bp.value, bp.value2, bp.baseline, bp.baseline2) == (152, 88, 140, 82) and bp.age == "2 days before this event"
    assert next(v for v in p.vitals if v.name == "Heart rate").value == 71        # the 99 bpm reading is after the event
    txt = P.render_text(p)
    assert "BP 152/88 mmHg, 2 days before this event (2019-05-03 10:12); personal baseline 140/82" in txt
    assert "Temperature 36.4 C, 2 days before this event" in txt and p.vitals_note.startswith("No recent vitals")


def test_recent_vitals_have_no_note(eng, fake):
    p = P.build_payload(eng, "a|2", "FALL", "p1", "2019-05-05", "2019-05-04T08:00:00", 1, {}, {}, None)
    assert p.vitals_note is None


def test_room_events_last3_within_6h_else_plain_sentence(eng, fake):
    p = P.build_payload(eng, "a|3", "FALL", "p1", "2019-05-05", "2019-05-05T10:00:00", 1, {}, {}, None)
    assert [e.room for e in p.recent_room_events] == ["Lounge", "Bedroom", "Bathroom"] and p.room_note is None   # Kitchen 03:00 is >6 h old
    assert "minutes before" in P.render_text(p)
    p = P.build_payload(eng, "a|4", "FALL", "p1", "2019-05-05", "2019-05-05T20:00:00", 1, {}, {}, None)
    assert p.recent_room_events == [] and "No room-sensor activity recorded in the 6 hours before the event (last recorded: 2019-05-05 12:00:00)" in P.render_text(p)


def test_fall_and_critical_vitals_are_distinct_alerts():
    f, c = fall_payload(), crit_payload(1, "2019-05-05T10:00:00")
    assert P.subject(f) != P.subject(c) and "fall" in P.subject(f).lower() and "vitals" in P.subject(c).lower()
    assert f.checklist == P.CHECKLIST and c.checklist == P.CHECKLIST_VITALS and f.checklist != c.checklist
    assert "Fall probability" in P.render_text(f) and "Fall probability" not in P.render_text(c)
    assert f.alert_id.split("|")[2] == "fall" and c.alert_id.split("|")[2] == "critical_vitals"
