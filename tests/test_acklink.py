"""Signed 'I have seen this' link in alert e-mails. FakeMailer only; the secret / URL below are test values."""
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from echos.alerts import acklink, payload as P
from echos.alerts.dispatcher import AlertDispatcher
from echos.alerts.mailer import FakeMailer
from echos.api import auth as A
from echos.api import services as V
from echos.api.app import create_app
from echos.config import load_config
from echos.orchestrator import state as S
from echos.team import lifecycle

SECRET, URL = "test-secret-0123456789", "https://echos.example.test"
AID = "p1|2019-05-05|fall|1"
TO = {"caregiver": "care@example.test", "clinician": "doc@example.test"}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv(acklink.SECRET_ENV, SECRET)
    monkeypatch.setenv(acklink.URL_ENV, URL)


def payload():
    return P.AlertPayload(alert_id=AID, alert_type="FALL", patient_id="p1", event_time="2019-05-05T10:00:00", event_id=1, fall_probability=0.9)


def make(tmp_path, monkeypatch=None):
    eng = S.get_engine(tmp_path / "a.db")
    with eng.begin() as c:
        S.insert_ignore(c, S.alert_state, alert_id=AID, patient_id="p1", type="FALL", state="SENT", created_ts=1.0, sent_ts=1.0, deadline_ts=1e12)
    return eng, TestClient(create_app(V.Ctx(eng=eng, root=tmp_path, cfg=load_config())))


def state(eng):
    return lifecycle.get(eng, AID).state


def test_token_roundtrip_and_contents(env):
    t = acklink.make_token(AID, 100, now=1000)
    assert acklink.verify_token(t, now=1050) == AID
    assert "p1" not in t and "fall" not in t        # only a base64 alert id + expiry + signature: no readable data


def test_expired_forged_and_malformed(env):
    t = acklink.make_token(AID, 100, now=1000)
    with pytest.raises(acklink.LinkError) as e:
        acklink.verify_token(t, now=1101)
    assert e.value.kind == "expired"
    a, exp, sig = t.split(".")
    for bad in (f"{a}.{int(exp) + 9999}.{sig}", f"{a}.{exp}.{'0' * 32}", f"{acklink._b64('other|id')}.{exp}.{sig}", "garbage", "a.b", ""):
        with pytest.raises(acklink.LinkError) as e:
            acklink.verify_token(bad, now=1050)
        assert e.value.kind == "invalid"


def test_other_secret_cannot_verify(env, monkeypatch):
    t = acklink.make_token(AID)
    monkeypatch.setenv(acklink.SECRET_ENV, "a-different-secret-987654")
    with pytest.raises(acklink.LinkError):
        acklink.verify_token(t)


def test_missing_or_short_secret_or_url_gives_no_link(monkeypatch):
    monkeypatch.delenv(acklink.SECRET_ENV, raising=False)
    monkeypatch.setenv(acklink.URL_ENV, URL)
    assert acklink.ack_url(AID) is None and acklink.make_token(AID) is None
    monkeypatch.setenv(acklink.SECRET_ENV, "short")
    assert acklink.ack_url(AID) is None
    monkeypatch.setenv(acklink.SECRET_ENV, SECRET)
    monkeypatch.delenv(acklink.URL_ENV)
    assert acklink.ack_url(AID) is None
    monkeypatch.setenv(acklink.URL_ENV, "javascript:alert(1)")
    assert acklink.ack_url(AID) is None
    with pytest.raises(acklink.LinkError) as e:
        monkeypatch.delenv(acklink.SECRET_ENV)
        acklink.verify_token("a.1.b")
    assert e.value.kind == "unconfigured"


def test_mail_carries_the_link_once_per_recipient_and_template_is_otherwise_unchanged(env, tmp_path):
    eng = S.get_engine(tmp_path / "m.db")
    m = FakeMailer()
    d = AlertDispatcher(eng, m, to=TO, backoff_s=[0.01], threaded=False)
    assert d.dispatch(payload())
    with eng.begin() as c:
        rows = c.execute(select(S.outbox)).fetchall()
    assert len(rows) == 2
    for r in rows:
        links = re.findall(rf"{re.escape(URL)}/api/ack/(\S+)", r.body)
        assert len(links) == 1 and acklink.verify_token(links[0]) == AID
        assert "I have seen this" in r.body_html and links[0] in r.body_html
    plain = P.render_text(payload())
    assert all(line in rows[0].body for line in plain.splitlines() if line.strip())      # the fixed template lines are all still there


def test_mail_without_secret_has_no_link(tmp_path, monkeypatch):
    monkeypatch.delenv(acklink.SECRET_ENV, raising=False)
    monkeypatch.setenv(acklink.URL_ENV, URL)
    eng = S.get_engine(tmp_path / "n.db")
    AlertDispatcher(eng, FakeMailer(), to=TO, threaded=False).dispatch(payload())
    with eng.begin() as c:
        for r in c.execute(select(S.outbox)):
            assert "/api/ack/" not in r.body and "http" not in (r.body_html or "") and "seen this" not in r.body


def test_endpoint_acknowledges_idempotently_and_shows_no_patient_data(env, tmp_path):
    eng, c = make(tmp_path)
    tok = acklink.make_token(AID)
    r = c.get(f"/api/ack/{tok}")
    assert r.status_code == 200 and "acknowledged" in r.text and "p1" not in r.text and r.headers["cache-control"] == "no-store"
    assert state(eng) == "ACKNOWLEDGED"
    ts = lifecycle.get(eng, AID).ack_ts
    r2 = c.get(f"/api/ack/{tok}")                                    # reused
    assert r2.status_code == 200 and lifecycle.get(eng, AID).ack_ts == ts


def test_endpoint_rejects_forged_expired_unknown(env, tmp_path):
    eng, c = make(tmp_path)
    tok = acklink.make_token(AID)
    a, e, s = tok.split(".")
    assert c.get(f"/api/ack/{a}.{e}.{'f' * 32}").status_code == 400
    assert c.get(f"/api/ack/{acklink._b64('p2|2019-05-05|fall|9')}.{e}.{s}").status_code == 400     # signature of another alert
    assert c.get(f"/api/ack/{acklink.make_token(AID, -5)}").status_code == 410
    assert c.get(f"/api/ack/{acklink.make_token('nobody|1|fall|1')}").status_code == 404
    assert state(eng) == "SENT"


def test_endpoint_without_secret_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.delenv(acklink.SECRET_ENV, raising=False)
    eng, c = make(tmp_path)
    assert c.get("/api/ack/a.1.b").status_code == 503 and state(eng) == "SENT"


def test_works_with_auth_on_without_a_login_but_other_routes_stay_closed(env, tmp_path, monkeypatch):
    monkeypatch.setenv(A.ENV["caregiver"], "t-care-123")
    monkeypatch.setenv("ECHOS_AUTH", "on")
    eng, c = make(tmp_path)
    assert c.get("/api/patients").status_code == 401
    assert c.get(f"/api/ack/{acklink.make_token(AID)}").status_code == 200 and state(eng) == "ACKNOWLEDGED"
    assert c.post(f"/api/alerts/{AID}/resolve").status_code == 401         # the link grants acknowledge only
    assert c.get(f"/api/ack/{acklink.make_token(AID)}/extra").status_code == 401
