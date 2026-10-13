"""Optional authentication and roles. Tokens are test values set in the environment for the test only."""
import pytest
from fastapi.testclient import TestClient

from echos.api import auth as A
from echos.api import services as V
from echos.api.app import create_app
from echos.config import load_config
from echos.orchestrator import state as S

TOK = {"caregiver": "t-care-123", "clinician": "t-clin-456", "operator": "t-oper-789"}


@pytest.fixture
def secured(tmp_path, monkeypatch):
    for r, t in TOK.items():
        monkeypatch.setenv(A.ENV[r], t)
    monkeypatch.setenv("ECHOS_AUTH", "on")
    eng = S.get_engine(tmp_path / "a.db")
    return TestClient(create_app(V.Ctx(eng=eng, root=tmp_path, cfg=load_config()))), eng


def h(role):
    return {"Authorization": f"Bearer {TOK[role]}"}


def test_rules_table():
    assert A.required_role("GET", "/api/patients") == "caregiver" and A.required_role("POST", "/api/alerts/x|y/ack") == "caregiver"
    assert A.required_role("GET", "/api/patients/p/reports/weekly/pdf") == "clinician" and A.required_role("POST", "/api/outbox/3/retry") == "clinician"
    assert A.required_role("POST", "/api/live/emulator") == "operator" and A.required_role("POST", "/api/sim/fall") == "operator" and A.required_role("GET", "/api/system/config") == "operator"
    assert A.required_role("POST", "/api/anything/new") == "operator"                 # unknown writes default to the strictest role


def test_nothing_without_a_token_and_wrong_tokens_fail(secured):
    c, _ = secured
    assert c.get("/api/patients").status_code == 401 and c.get("/api/live/status").status_code == 401
    assert c.get("/api/patients", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert c.get("/api/auth/me").json() == {"auth_enabled": True, "role": None}
    assert c.post("/api/auth/login", json={"token": "nope"}).status_code == 401
    assert c.get("/api/events").status_code == 401                                   # the event stream needs ?token=


def test_each_role_sees_what_it_should(secured):
    c, _ = secured
    for role, tok in TOK.items():
        r = c.post("/api/auth/login", json={"token": tok})
        assert r.json() == {"auth_enabled": True, "role": role}
        assert c.get("/api/auth/me", headers=h(role)).json()["role"] == role
    assert c.get("/api/patients", headers=h("caregiver")).status_code == 200
    assert c.get("/api/patients/x/reports/weekly", headers=h("caregiver")).status_code == 403
    assert c.get("/api/outbox", headers=h("caregiver")).status_code == 403 and c.get("/api/outbox", headers=h("clinician")).status_code == 200
    assert c.get("/api/system/config", headers=h("clinician")).status_code == 403 and c.get("/api/system/config", headers=h("operator")).status_code == 200
    assert c.post("/api/live/emulator", headers=h("clinician"), json={"action": "pause"}).status_code == 403
    assert c.post("/api/sim/control", headers=h("caregiver"), json={"action": "pause"}).status_code == 403


def test_acknowledgement_records_the_logged_in_role_not_client_text(secured):
    import time
    c, eng = secured
    with eng.begin() as con:
        S.insert_ignore(con, S.alerts, alert_id="a|1", patient_id="p", type="fall", time=time.time(), payload="{}", status="SENT", event_id=1, event_time="2019-05-05T10:00:00")
        S.insert_ignore(con, S.alert_state, alert_id="a|1", patient_id="p", type="FALL", state="SENT", created_ts=1.0)
    r = c.post("/api/alerts/a|1/ack", headers=h("caregiver"), json={"by": "the president"})
    assert r.status_code == 200 and r.json()["ack_by"] == "caregiver"
    assert c.post("/api/alerts/a|1/resolve", headers=h("caregiver"), json={}).json()["state"] == "RESOLVED"


def test_login_is_rate_limited_and_ingest_keeps_its_own_token(secured, monkeypatch):
    c, _ = secured
    for _ in range(8):
        assert c.post("/api/auth/login", json={"token": "bad"}).status_code == 401
    assert c.post("/api/auth/login", json={"token": "bad"}).status_code == 429
    ev = {"events": [{"patient_id": "px", "ts": "2019-05-20T08:00:00", "kind": "heartbeat"}]}
    assert c.post("/api/ingest/events", json=ev).status_code == 200                  # no bearer needed: devices are not users
    monkeypatch.setenv("ECHOS_INGEST_TOKEN", "dev-tok")
    assert c.post("/api/ingest/events", json=ev).status_code == 403
    assert c.post("/api/ingest/events", json=ev, headers={"X-Echos-Token": "dev-tok"}).status_code == 200


def test_auth_off_is_the_demo_default(tmp_path):
    eng = S.get_engine(tmp_path / "o.db")
    c = TestClient(create_app(V.Ctx(eng=eng, root=tmp_path, cfg=load_config())))
    assert c.get("/api/patients").status_code == 200 and c.get("/api/auth/me").json() == {"auth_enabled": False, "role": "operator"}
