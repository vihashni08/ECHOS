"""Alert path never leaks the SMTP password or a full address into outbox errors or logs. FakeMailer only: no SMTP."""
import logging

from sqlalchemy import select

from echos.alerts import payload as P
from echos.alerts.dispatcher import AlertDispatcher, _redact
from echos.alerts.mailer import FakeMailer
from echos.orchestrator import state as S

from tests.test_email_retry import Clock  # noqa: E402

PW, USER, ADDR = "s3cr3t-smtp-pw-456", "smtp-user-789", "care@example.test"


class Leaky(FakeMailer):
    def send(self, to, subject, body, body_html=None, attachments=None):
        raise ConnectionError(f"login {USER}/{PW} refused; recipient {to} rejected ({ADDR})")


def test_outbox_error_and_logs_have_no_password_or_full_address(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("ECHOS_SMTP_PASSWORD", PW)
    monkeypatch.setenv("ECHOS_SMTP_USER", USER)
    eng = S.get_engine(tmp_path / "h.db")
    clock = Clock()
    d = AlertDispatcher(eng, Leaky(), to={"caregiver": ADDR, "clinician": "doc@example.test"}, threaded=False, clock=clock)
    with caplog.at_level(logging.DEBUG):
        d.dispatch(P.AlertPayload(alert_id="p1|2019-05-05|critical_vitals|1", patient_id="p1", event_time="2019-05-05T10:00:00", event_id=1))
        d.drive(clock() + 2000)
    with eng.begin() as c:
        errs = [r.error for r in c.execute(select(S.outbox)) if r.error]
    blob = " ".join(errs) + caplog.text
    assert errs and PW not in blob and USER not in blob and ADDR not in blob and "doc@example.test" not in blob and "ca***@example.test" in blob


def test_redact_masks_addresses_and_secrets(monkeypatch):
    monkeypatch.setenv("ECHOS_SMTP_PASSWORD", PW)
    out = _redact(f"bad {PW} for first.last+x@mail.example.org")
    assert PW not in out and "first.last+x@mail.example.org" not in out and "fi***@mail.example.org" in out
