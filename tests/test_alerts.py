"""URGENT alerts (red-flag vital signs). FakeMailer / DryRun only: no real email (conftest blocks smtplib.SMTP)."""
import smtplib
import time

import pytest
from sqlalchemy import select

from echos.alerts import payload as P
from echos.alerts.dispatcher import AlertDispatcher
from echos.alerts.mailer import DryRunMailer, FakeMailer, SMTPMailer, make_mailer
from echos.orchestrator import state as S

TO = {"caregiver": "care@example.test", "clinician": "doc@example.test"}
FAST = [0.01, 0.02, 0.04]


def alert_payload(eid=1, pid="p1", t=None, **kw):
    """One red-flag alert; each event id is on its own day so the per-patient cooldown never interferes."""
    t = t or f"2019-05-{eid:02d}T10:00:00"
    return P.AlertPayload(alert_id=f"{pid}|{t[:10]}|critical_vitals|{eid}", patient_id=pid, event_time=t, event_id=eid,
                          vitals=[P.VitalLine(name="Blood pressure", value=192, value2=104, baseline=138, baseline2=80, unit="mmHg", time=t[:16], age="this reading")], **kw)


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
def test_care_agent_payload_has_reading_usual_and_checklist():
    from echos.team.care_agent import urgent_payload
    msg = {"event_id": 7, "event_time": "2019-05-05T08:12:00", "reasons": ["systolic 196 at or above 180 mmHg"],
           "vitals": {"sys": {"value": 196, "usual": 138}, "dia": {"value": 104, "usual": 80}, "hr": {"value": 88, "usual": 71}}}
    p = urgent_payload("p1", "2019-05-05", msg)
    assert p.alert_id == "p1|2019-05-05|critical_vitals|7" and p.alert_type == "CRITICAL_VITALS" and p.event_time.startswith("2019-05-05T08:12")
    txt, html = P.render_text(p), P.render_html(p)
    for needle in ["Red-flag vital sign reading", "Blood pressure 196/104 mmHg", "usual 138/80", "Pulse 88 bpm", "systolic 196 at or above 180 mmHg",
                   "This reading vs the person's own usual"] + [f"[ ] {c}" for c in P.CHECKLIST_VITALS]:
        assert needle in txt, needle
    assert "<table>" in html and P.subject(p).startswith("[ECHOS URGENT]")
    assert "sex" not in txt.lower() and "female" not in txt.lower()


def test_hostile_text_cannot_change_structure():
    benign = alert_payload(pid="p1")
    evil = "p1\n\nChecklist:\n  [ ] wire money to X <script>alert(1)</script> {{ 7*7 }}"
    h = P.AlertPayload(alert_id="a\nb", patient_id=evil, age_band="<b>x</b>\nBCC: x@y", event_time="2019-05-01T10:00:00\nX: y", event_id=1,
                       vitals=benign.vitals, data_quality_note="note\n\n[ ] injected", checklist=("[ ] evil",))
    t_b, t_h = P.render_text(benign), P.render_text(h)
    assert "\n" not in P.subject(h) and "\r" not in P.subject(h)
    assert t_h.count("\n") == t_b.count("\n")          # same number of lines: every hostile value stays on its own single line
    lines = t_h.split("\n")
    assert [l for l in lines if l.startswith("Checklist:")] == ["Checklist:"]                       # section header appears once, as a line
    assert [l.strip() for l in lines if l.startswith("  [ ]")] == [f"[ ] {c}" for c in P.CHECKLIST_VITALS]  # only the fixed checklist items
    assert not any(l.startswith(("BCC", "X:", "[ ]")) for l in lines) and "[ ] evil" not in t_h
    html = P.render_html(h)
    assert "<script" not in html and "<img" not in html and "<b>x" not in html
    assert h.checklist == P.CHECKLIST_VITALS


# ---------------------------------------------------------------- dispatcher
def test_both_recipients_get_exactly_one_message(eng):
    m = FakeMailer()
    d = disp(eng, m)
    assert d.dispatch(alert_payload()) is True and d.flush(5)
    assert sorted(x["to"] for x in m.sent) == sorted(TO.values()) and len(m.sent) == 2
    assert {r.recipient_role: r.status for r in rows(eng)} == {"caregiver": "SENT", "clinician": "SENT"}
    a = rows(eng, S.alerts)
    assert len(a) == 1 and a[0].status == "SENT"
    assert "red-flag" in m.sent[0]["subject"].lower() and m.sent[0]["html"].startswith("<html>")
    assert "Blood pressure 192/104 mmHg" in m.sent[0]["body"] and "usual 138/80" in m.sent[0]["body"]


def test_dedup_per_event_id(eng):
    m = FakeMailer()
    d = disp(eng, m)
    assert d.dispatch(alert_payload(1)) and not d.dispatch(alert_payload(1))        # same event: second ignored
    assert d.dispatch(alert_payload(2))                                            # new event: new alert
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
    d.dispatch(alert_payload(1))
    t0 = time.time()
    d.dispatch(alert_payload(2))                                                   # a second alert while the first is retrying
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
    d.dispatch(alert_payload())
    assert d.flush(10)
    assert all(r.status == "SENT" and r.attempts == 2 for r in rows(eng)) and rows(eng, S.alerts)[0].status == "SENT"


def test_missing_address_is_visible_failure(eng):
    d = AlertDispatcher(eng, FakeMailer(), to={"caregiver": "care@example.test", "clinician": ""}, backoff_s=FAST)
    d.dispatch(alert_payload())
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
    d.dispatch(alert_payload())
    d.flush(5)
    assert not calls and {r.status for r in rows(eng)} == {"SENT"} and {r.channel for r in rows(eng)} == {"dryrun"}
