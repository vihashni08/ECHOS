"""Multi-agent layer: Case Investigator, Care Coordinator (alert lifecycle), message bus. FakeMailer only; fake clock, no sleeping."""
import json

import pytest
from sqlalchemy import select

from echos.alerts.dispatcher import AlertDispatcher
from echos.alerts.mailer import FakeMailer
from echos.components import COMPONENTS
from echos.orchestrator import state as S
from echos.team import bus, lifecycle
from echos.team.coordinator import CareCoordinator
from echos.team.investigator import Investigator, load_case_file
from tests.test_alerts import FAST, TO, crit_payload, fall_payload

TOE = {**TO, "escalation": "boss@example.test"}


@pytest.fixture
def eng(tmp_path):
    return S.get_engine(tmp_path / "t.db")


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def setup(eng, mailer=None, to=TOE, cfg=None):
    clock = Clock()
    d = AlertDispatcher(eng, mailer or FakeMailer(), to=to, backoff_s=FAST, clock=clock)
    c = CareCoordinator(eng, d, clock=clock, cfg=cfg or {"ack_timeout_s": {"FALL": 300, "CRITICAL_VITALS": 1800}, "checkin_after_ack_s": 600,
                                                         "review_window_days": 5, "review_min_days": 3})
    return clock, d, c


def drain(d):
    assert d.flush(10)


def state(eng, aid):
    return lifecycle.get(eng, aid)


def test_registry_has_detectors_and_real_agents():
    kinds = {c["id"]: c["kind"] for c in COMPONENTS}
    assert {k for k, v in kinds.items() if v == "detector"} == {"quality", "health_monitoring", "fall"}
    assert {k for k, v in kinds.items() if v == "agent"} == {"triage", "investigator", "coordinator", "report"}
    assert all(c["role"] and c["on_failure"] and c["never"] for c in COMPONENTS)


# ---------------------------------------------------------------- Care Coordinator
def test_alert_goes_new_sent_and_escalates_when_nobody_acknowledges(eng):
    clock, d, co = setup(eng)
    p = fall_payload()
    assert d.dispatch(p)
    drain(d)
    assert co.step()["registered"] == 1 and state(eng, p.alert_id).state == "SENT"
    deadline = state(eng, p.alert_id).deadline_ts
    clock.t = deadline - 1
    assert co.step()["escalated"] == 0 and state(eng, p.alert_id).state == "SENT"
    clock.t = deadline + 1
    assert co.step()["escalated"] == 1
    drain(d)
    st = state(eng, p.alert_id)
    assert st.state == "ESCALATED" and st.escalated_ts == deadline + 1
    with eng.begin() as c:
        esc = c.execute(select(S.outbox).where(S.outbox.c.recipient_role == "escalation")).fetchall()
    assert len(esc) == 1 and esc[0].status == "SENT" and esc[0].subject.startswith("[ESCALATED") and "No one has acknowledged" in esc[0].body
    assert co.step()["escalated"] == 0                                    # once only, even if the loop runs again / restarts
    assert [m for m in bus.poll(eng, "all", ["alert_escalated"])][0]["payload"]["alert_id"] == p.alert_id


def test_acknowledgement_before_the_deadline_prevents_escalation_and_opens_a_checkin(eng):
    clock, d, co = setup(eng)
    p = fall_payload()
    d.dispatch(p)
    drain(d)
    co.step()
    assert lifecycle.acknowledge(eng, p.alert_id, "daughter", now=clock.t + 30) == "ACKNOWLEDGED"
    clock.t = state(eng, p.alert_id).deadline_ts + 500
    assert co.step()["tasks"] == 1 and state(eng, p.alert_id).state == "ACKNOWLEDGED"
    with eng.begin() as c:
        assert c.execute(select(S.outbox).where(S.outbox.c.recipient_role == "escalation")).first() is None
        task = c.execute(select(S.care_task)).first()
    assert task.kind == "CHECK_IN" and task.status == "OPEN" and task.alert_id == p.alert_id
    assert co.step()["tasks"] == 0                                         # not duplicated
    assert lifecycle.acknowledge(eng, p.alert_id, "someone else") == "ACKNOWLEDGED"
    assert lifecycle.resolve(eng, p.alert_id, "false alarm") == "RESOLVED"
    with eng.begin() as c:
        assert c.execute(select(S.care_task.c.status)).scalar() == "DONE"
    assert lifecycle.acknowledge(eng, "unknown") is None


def test_unreachable_caregiver_escalates_immediately(eng):
    clock, d, co = setup(eng)
    with eng.begin() as c:       # an alert whose caregiver mail ended FAILED (no address / SMTP down past the retry window)
        c.execute(S.alerts.insert().values(alert_id="a1", patient_id="p1", type="fall", time=clock.t, payload="{}", status="FAILED", event_id=1, event_time="2019-05-05T10:00:00"))
        c.execute(S.outbox.insert().values(alert_id="a1", recipient_role="caregiver", address="", channel="dry", subject="s", body="b", status="FAILED", attempts=3, error="x",
                                           event_time="2019-05-05T10:00:00", queued_time=clock.t))
    assert co.step()["escalated"] == 1                       # tracked and escalated in the same pass: no waiting for a timeout
    assert state(eng, "a1").state == "ESCALATED" and "escalating immediately" in state(eng, "a1").note
    drain(d)


def test_missing_escalation_address_is_visible_not_silent(eng):
    clock, d, co = setup(eng, to=TO)
    p = crit_payload(1, "2019-05-05T10:00:00")
    d.dispatch(p)
    drain(d)
    co.step()
    clock.t += 2000
    co.step()
    with eng.begin() as c:
        row = c.execute(select(S.outbox).where(S.outbox.c.recipient_role == "escalation")).first()
    assert row.status == "FAILED" and "no escalation address" in row.error and state(eng, p.alert_id).state == "ESCALATED"


def test_restart_keeps_state(eng):
    clock, d, co = setup(eng)
    p = fall_payload()
    d.dispatch(p)
    drain(d)
    co.step()
    co2 = CareCoordinator(eng, d, clock=clock, cfg=co.cfg)                  # a fresh coordinator: nothing lives in memory
    clock.t += 1000
    assert co2.step()["escalated"] == 1 and co2.step()["escalated"] == 0


def test_repeated_changes_open_one_clinician_review(eng):
    clock, d, co = setup(eng)
    with eng.begin() as c:
        for day, st in [("2019-05-01", "MONITOR"), ("2019-05-02", "NORMAL"), ("2019-05-03", "MONITOR"), ("2019-05-04", "MONITOR")]:
            S.upsert(c, S.case_state, ["patient_id", "day"], patient_id="p1", day=day, status=st, data_gap=False, days_since_vitals=0, reasons="[]", report=None, updated_ts=0)
    bus.post(eng, "investigator", "coordinator", "case_file_ready", "p1", "2019-05-04", {})
    assert co.step()["tasks"] == 1
    bus.post(eng, "investigator", "coordinator", "case_file_ready", "p1", "2019-05-04", {})
    assert co.step()["tasks"] == 0
    with eng.begin() as c:
        t = c.execute(select(S.care_task)).first()
    assert t.kind == "CLINICIAN_REVIEW" and "3 changed days" in t.title


# ---------------------------------------------------------------- Case Investigator
def results(vitals=None, beh=None, fall=None, quality=None):
    return {"quality": {"evidence": {"quality_checked": True, **(quality or {})}},
            "health": {"vitals": vitals or {"available": True, "flag": False}, "behaviour": beh or {"available": True, "flag": False}, "degraded": False},
            "fall": fall or {"score": 0.0, "flag": False, "evidence": {}}}


def test_normal_cases_get_no_case_file(eng):
    assert Investigator(eng).investigate("p1", "2019-05-05", {"status": "NORMAL"}, results()) is None


def test_confirmed_fall_is_strong_and_leaves_a_trace(eng):
    r = results(fall={"score": 0.97, "flag": True, "evidence": {"post_fall_inactive": True}})
    f = Investigator(eng).investigate("p1", "2019-05-05", {"status": "URGENT", "urgent_types": ["fall"], "reasons": ["fall p=0.97 + post-fall inactivity"]}, r)
    assert f["evidence_level"] == "STRONG" and "fall was detected" in f["headline"] and "not moved" in f["headline"]
    assert [s["tool"] for s in f["trace"]][:2] == ["read_detectors", "check_history"] and len(f["trace"]) <= 4
    assert any("Contact the person" in x for x in f["next_checks"])
    assert load_case_file(eng, "p1", "2019-05-05")["headline"] == f["headline"]
    topics = {(m["recipient"], m["topic"]) for m in bus.poll(eng, "coordinator") + bus.poll(eng, "report")}
    assert topics == {("coordinator", "case_file_ready"), ("report", "case_file_ready")}


def test_single_weak_flag_with_a_data_caveat_is_limited_and_never_medical_advice(eng):
    r = results(beh={"available": True, "flag": True}, quality={"sensor_gap": True})
    f = Investigator(eng).investigate("p1", "2019-05-05", {"status": "MONITOR", "data_gap": True, "reasons": ["agitation p=0.30"], "urgent_types": []}, r)
    assert f["evidence_level"] == "LIMITED" and f["caveats"]
    text = json.dumps(f).lower()
    assert "diagnos" not in text.replace("not a diagnosis", "") and "medication" not in text and "dose" not in text


def test_a_repeat_with_persistence_is_moderate_and_mentions_the_streak(eng):
    with eng.begin() as c:
        for day in ("2019-05-03", "2019-05-04"):
            S.upsert(c, S.case_state, ["patient_id", "day"], patient_id="p1", day=day, status="MONITOR", data_gap=False, days_since_vitals=0, reasons="[]", report=None, updated_ts=0)
    r = results(vitals={"available": True, "flag": True, "persistence": {"persistent": True}})
    f = Investigator(eng).investigate("p1", "2019-05-05", {"status": "MONITOR", "reasons": ["vitals alert p=0.5"], "urgent_types": []}, r)
    assert f["evidence_level"] == "MODERATE" and f["history"]["streak_before_today"] == 2 and any("day 3 in a row" in x for x in f["next_checks"])


def test_old_unacknowledged_alerts_expire_instead_of_paging_the_second_contact(eng):
    clock, d, co = setup(eng, cfg={"ack_timeout_s": {"FALL": 300}, "stale_after_s": 3600, "checkin_after_ack_s": 600})
    p = fall_payload()
    d.dispatch(p)
    drain(d)
    co.step()
    clock.t += 7200                                                      # the runtime was down for two hours
    assert co.step()["escalated"] == 0
    st = state(eng, p.alert_id)
    assert st.state == "EXPIRED" and "too old" in st.note
    with eng.begin() as c:
        assert c.execute(select(S.outbox).where(S.outbox.c.recipient_role == "escalation")).first() is None


def test_components_doc_is_generated_from_the_registry():
    from pathlib import Path
    import sys
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from scripts.make_components_doc import render
    assert (root / "docs" / "COMPONENTS.md").read_text() == render(), "run: PYTHONPATH=. python scripts/make_components_doc.py"


def test_report_agent_uses_the_case_file_and_consumes_its_message(eng, tmp_path):
    from echos.reports.agent import ReportAgent
    from tests.test_reports import facts
    f = facts()
    Investigator(eng).investigate(f["patient_id"], f["day"], {"status": "MONITOR", "reasons": ["vitals alert p=0.50"], "urgent_types": []},
                                  results(vitals={"available": True, "flag": True, "persistence": {"persistent": True}}))
    out = ReportAgent(eng, None, root=tmp_path).daily(f)
    assert "## Case summary" in out["text"] and "differs from this person's usual" in out["text"] and "not a probability" in out["text"]
    assert not [m for m in bus.poll(eng, "report") if m["patient_id"] == f["patient_id"]]          # the message was consumed
