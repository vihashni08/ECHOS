"""API: components registry, alert lifecycle (ack / resolve), care tasks, case file, agent messages. Same fixture database as tests/test_api.py."""
from urllib.parse import quote

from echos.api import models as M
from echos.orchestrator import state as S
from echos.team import bus
from echos.team.coordinator import CareCoordinator
from echos.team.investigator import Investigator
from tests.test_api import PID, api  # noqa: F401
from tests.test_weekly import NOPM, WEEK, disp, world  # noqa: F401

AID = f"{PID}|2019-05-09|fall|5"


def test_components_describe_detectors_and_agents(api):
    c, _ = api
    comps = [M.ComponentOut.model_validate(x) for x in c.get("/api/components").json()]
    by = {x.id: x for x in comps}
    assert {x.id for x in comps if x.kind == "detector"} == {"quality", "health_monitoring", "fall"}
    assert {x.id for x in comps if x.kind == "agent"} == {"triage", "investigator", "coordinator", "report"}
    assert by["quality"].status == "running" and by["coordinator"].status == "missing" and all(x.never for x in comps)


def test_alert_lifecycle_through_the_api(api):
    c, ctx = api
    assert c.post(f"/api/alerts/{quote(AID, safe='')}/ack").status_code in (404, 200)      # the fixture alert row may not be registered yet
    # fixture has outbox rows only; give it an alerts row and let the Care Coordinator register it
    import time
    with ctx.eng.begin() as con:
        con.execute(S.outbox.update().where(S.outbox.c.alert_id == AID).values(sent_time=time.time()))      # the fixture's sent_time is 1970
        S.insert_ignore(con, S.alerts, alert_id=AID, patient_id=PID, type="fall", time=time.time(), payload="{}", status="SENT", event_id=5, event_time="2019-05-09T14:02:11")
    CareCoordinator(ctx.eng, None).step()
    items = c.get("/api/alerts").json()["items"]
    a = next(x for x in items if x["alert_id"] == AID)
    assert a["state"] == "SENT" and a["deadline_ts"] is not None and a["ack_ts"] is None
    r = c.post(f"/api/alerts/{quote(AID, safe='')}/ack", json={"by": "daughter"})
    assert r.status_code == 200 and r.json()["state"] == "ACKNOWLEDGED" and r.json()["ack_by"] == "daughter"
    assert c.post(f"/api/alerts/{quote(AID, safe='')}/ack").json()["state"] == "ACKNOWLEDGED"        # idempotent
    CareCoordinator(ctx.eng, None).step()
    tasks = c.get("/api/tasks?status=OPEN").json()
    assert tasks["total"] == 1 and tasks["items"][0]["kind"] == "CHECK_IN"
    assert c.post(f"/api/alerts/{quote(AID, safe='')}/resolve", json={"note": "was fine"}).json()["state"] == "RESOLVED"
    assert c.get("/api/tasks?status=OPEN").json()["total"] == 0
    assert c.post("/api/alerts/nope/ack").status_code == 404


def test_case_file_and_message_bus(api):
    c, ctx = api
    assert c.get(f"/api/patients/{PID}/case").status_code == 404
    Investigator(ctx.eng).investigate(PID, "2019-05-09", {"status": "URGENT", "urgent_types": ["fall"], "reasons": ["fall p=0.99"]},
                                      {"quality": {"evidence": {"quality_checked": True}}, "health": {"vitals": {}, "behaviour": {}},
                                       "fall": {"score": 0.99, "flag": True, "evidence": {"post_fall_inactive": True}}})
    f = M.CaseFileOut.model_validate(c.get(f"/api/patients/{PID}/case").json())
    assert f.day == "2019-05-09" and f.evidence_level == "STRONG" and f.trace[0]["tool"] == "read_detectors"
    assert c.get(f"/api/patients/{PID}/case?day=2019-01-01").status_code == 404
    bus.post(ctx.eng, "triage", "investigator", "case_opened", PID, "2019-05-09", {})
    msgs = [M.AgentMessageOut.model_validate(x) for x in c.get("/api/agents/messages?limit=10").json()]
    assert {m.topic for m in msgs} >= {"case_opened", "case_file_ready"} and {m.sender for m in msgs} >= {"triage", "investigator"}
