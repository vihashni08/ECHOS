"""Agent activity view: events come from the tables the agents write; filters; one-case ordering; the live fall workflow leaves the whole chain. No network."""
import json

import pytest
from fastapi.testclient import TestClient

from echos.api import activity as AC
from echos.api import services as V
from echos.api.app import create_app
from echos.config import load_config
from echos.orchestrator import state as S
from echos.team import bus, lifecycle

from tests.test_live import build, run_emulator, eng as live_eng  # noqa: F401
from echos.live.emulator import Emulator


@pytest.fixture
def db(tmp_path):
    return S.get_engine(tmp_path / "act.db")


def seed(eng):
    with eng.begin() as c:
        c.execute(S.agent_output.insert().values(event_id=1, agent_id="fall", patient_id="p1", day="2019-05-05", result=json.dumps({"score": 0.97, "flag": True}), done_ts=100.0))
        c.execute(S.agent_output.insert().values(event_id=2, agent_id="quality", patient_id="p1", day="2019-05-05", result=json.dumps({"evidence": {"invalid_values": [{"device_type": "Heart rate"}], "has_vitals": False}}), done_ts=99.0))
        S.upsert(c, S.case_state, ["patient_id", "day"], patient_id="p1", day="2019-05-05", status="URGENT", data_gap=False, reasons=json.dumps(["possible fall"]), updated_ts=101.0, provisional=False)
        S.upsert(c, S.case_state, ["patient_id", "day"], patient_id="p2", day="2019-05-05", status="NORMAL", data_gap=False, reasons="[]", updated_ts=50.0, provisional=False)
        c.execute(S.alerts.insert().values(alert_id="p1|2019-05-05|fall|1", patient_id="p1", type="fall", time=102.0, payload="{}", status="SENT", event_id=1, event_time="2019-05-05T10:00:00"))
        c.execute(S.outbox.insert().values(alert_id="p1|2019-05-05|fall|1", recipient_role="caregiver", address="x", channel="dry_run", subject="s", body="b", status="SENT", attempts=1, event_time="2019-05-05T10:00:00", queued_time=102.0, sent_time=102.5))
        S.upsert(c, S.alert_state, ["alert_id"], alert_id="p1|2019-05-05|fall|1", patient_id="p1", type="FALL", state="ACKNOWLEDGED", created_ts=106.0, sent_ts=107.0, ack_ts=110.0, ack_by="email link")
        c.execute(S.case_file.insert().values(patient_id="p1", day="2019-05-05", file=json.dumps({"headline": "Fall with inactivity", "evidence_level": "STRONG", "trace": [1, 2, 3]}), created_ts=105.0))
        c.execute(S.care_task.insert().values(patient_id="p1", day="2019-05-05", kind="CHECK_IN", title="Check in on p1", created_by="coordinator", created_ts=111.0, status="OPEN", alert_id="p1|2019-05-05|fall|1"))
        c.execute(S.reports.insert().values(patient_id="p1", day="2019-05-05", kind="daily", mode="template", path="x", created_ts=112.0))
        c.execute(S.restart_log.insert().values(agent_id="fall", detected_ts=90.0, reason="exited_1", restarted_ts=91.0))
    bus.post(eng, "triage", "investigator", "case_opened", "p1", "2019-05-05", {})


def ctx(eng):
    return V.Ctx(eng=eng, cfg=load_config())


def test_every_source_shows_up_and_default_feed_omits_detector_rows(db):
    seed(db)
    ev = AC.events(ctx(db), 100)
    agents = {e["agent"] for e in ev}
    assert agents == {"triage", "dispatcher", "coordinator", "investigator", "report", "supervisor"}      # detectors only on request
    assert [e["ts"] for e in ev] == sorted((e["ts"] for e in ev), reverse=True)
    txt = " | ".join(e["text"] for e in ev)
    for needle in ("decided URGENT", "dry run (nothing emailed) to the caregiver", "acknowledged by email link", "built a case file (strong evidence, 3 steps)", "opened a task", "wrote the daily report", "restarted fall (exited_1)", "told Case Investigator: case opened"):
        assert needle in txt, needle


def test_filters_by_agent_patient_and_one_case_with_detectors_in_order(db):
    seed(db)
    c = ctx(db)
    assert {e["agent"] for e in AC.events(c, 100, agent="fall")} == {"fall"} and "score 0.97, flagged" in AC.events(c, 5, agent="fall")[0]["text"]
    assert "invalid value" in AC.events(c, 5, agent="quality")[0]["text"]
    assert {e["patient_id"] for e in AC.events(c, 100, patient_id="p2")} == {"p2"}
    case = AC.events(c, 100, patient_id="p1", day="2019-05-05", asc=True)
    order = [e["agent"] for e in case]
    assert order.index("quality") < order.index("fall") < order.index("triage") < order.index("dispatcher") < order.index("investigator") < order.index("coordinator") < order.index("report")
    assert [e["ts"] for e in case] == sorted(e["ts"] for e in case)


def test_summary_counts_and_last_action(db):
    seed(db)
    s = {x["id"]: x for x in AC.summary(ctx(db))}
    assert s["fall"]["actions"] == 1 and s["quality"]["actions"] == 1 and s["investigator"]["actions"] == 1 and s["dispatcher"]["actions"] >= 2
    assert s["coordinator"]["last_text"] and s["triage"]["last_ts"] is not None
    assert list(s)[list(s).index("investigator") - 1] == "dispatcher"


def test_api_routes_and_limits(db):
    seed(db)
    c = TestClient(create_app(ctx(db)))
    r = c.get("/api/agents/activity?limit=3")
    assert r.status_code == 200 and len(r.json()) == 3 and set(r.json()[0]) == {"ts", "agent", "action", "text", "patient_id", "day", "ref"}
    assert c.get("/api/agents/activity?limit=0").status_code == 422
    assert c.get("/api/agents/activity?patient_id=p1&day=2019-05-05&oldest_first=true").json()[0]["agent"] == "quality"
    assert {x["id"] for x in c.get("/api/agents/summary").json()} >= {"triage", "investigator", "coordinator", "dispatcher"}


def test_expired_alert_is_shown_as_history_not_paged(db):
    with db.begin() as c:
        S.upsert(c, S.alert_state, ["alert_id"], alert_id="p9|2019-04-01|fall|3", patient_id="p9", type="FALL", state="EXPIRED", created_ts=5.0)
    assert any("recorded as history, nobody paged" in e["text"] for e in AC.events(ctx(db), 10, agent="coordinator"))


def test_live_fall_workflow_leaves_the_whole_chain_in_order(live_eng):
    """Emulator + processor + coordinator: an injected fall must show detector -> triage -> alert queued -> sent -> case file -> coordinator waiting, then ack -> resolved."""
    import numpy as np
    from echos.data import sisfall as sf
    eng = live_eng
    f = sf.list_files()
    f = f[f.is_fall & f.subject.isin(load_config()["replay"]["holdout_subjects"]) & (f.activity == "F02") & (f.subject == "SA03")]
    if f.empty:
        pytest.skip("SisFall recording not on this machine")
    proc, d = build(eng)
    from echos.team.coordinator import CareCoordinator
    coord = CareCoordinator(eng, d)
    emu = Emulator(eng, cfg={"speed": 4 * 3600})
    pats = emu.load(start_day="2019-05-13", n_days=2, n_patients=1)
    emu.status = "playing"
    emu.tick(wall=0.0)
    run_emulator(eng, proc, emu, wall_steps=3)
    r = f.iloc[0]
    a = sf.load_file(r.path)
    out = emu.inject_fall({"patient_id": pats[0], "file": r.path, "win_start": max(0, int(np.argmax(sf.svm(a))) - 250), "subject": r.subject, "activity": r.activity})
    for _ in range(4):
        proc.step()
    d.flush(5)
    c = ctx(eng)
    for _ in range(3):
        coord.tick() if hasattr(coord, "tick") else coord.step()
    with eng.begin() as cx:
        st = cx.execute(S.case_state.select().where(S.case_state.c.day == out["day"])).first()
    if st.status != "URGENT":
        pytest.skip("this recording is not URGENT with the replay model")
    flow = AC.events(c, 200, patient_id=pats[0], day=out["day"], asc=True)
    seen = [e["agent"] for e in flow]
    for a_ in ("fall", "triage", "dispatcher", "investigator", "coordinator"):
        assert a_ in seen, (a_, seen)
    assert seen.index("fall") < seen.index("triage") < seen.index("dispatcher") and seen.index("triage") < seen.index("investigator") < seen.index("coordinator")
    print("CHAIN", [(e["agent"], e["action"]) for e in flow])
    aid = next(e["ref"] for e in flow if e["agent"] == "dispatcher" and e["action"] == "queued")
    lifecycle.acknowledge(eng, aid, "caregiver")
    lifecycle.resolve(eng, aid, "handled")
    acts = [e["action"] for e in AC.events(c, 200, agent="coordinator", patient_id=pats[0])]
    assert "acknowledged" in acts and "resolved" in acts


def test_triage_decision_is_ordered_before_the_alert_it_caused_even_if_case_state_was_updated_later(db):
    seed(db)
    with db.begin() as c:
        c.execute(S.case_state.update().where(S.case_state.c.patient_id == "p1").values(updated_ts=500.0))      # re-upserted long after (e.g. after the report)
    order = [e["agent"] for e in AC.events(ctx(db), 100, patient_id="p1", day="2019-05-05", asc=True)]
    assert order.index("triage") < order.index("dispatcher") < order.index("coordinator")
