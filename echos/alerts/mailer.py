"""Mailers. SMTPMailer (STARTTLS, env ECHOS_SMTP_HOST/PORT/USER/PASSWORD), DryRunMailer (outbox only: nothing leaves the
machine) and FakeMailer (tests). `make_mailer` returns an SMTPMailer ONLY when config alerts.send_real is true; otherwise
SMTP is never touched. Secrets are never printed or logged."""
from __future__ import annotations

import os
import smtplib
import ssl
import threading
from email.message import EmailMessage

from echos.config import ROOT, load_config


def load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=False)
    except Exception:  # noqa: BLE001 - python-dotenv is optional at runtime
        pass


def recipients() -> dict:
    """{'caregiver': address, 'clinician': address} from the environment ('' when unset: the outbox row then FAILS visibly)."""
    load_env()
    return {"caregiver": os.environ.get("ECHOS_CAREGIVER_EMAIL", "").strip(), "clinician": os.environ.get("ECHOS_CLINICIAN_EMAIL", "").strip()}


class Mailer:
    channel = "base"

    def send(self, to: str, subject: str, body: str, body_html: str | None = None) -> None:   # raises on failure
        raise NotImplementedError


class DryRunMailer(Mailer):
    """Outbox only. Marks the row delivered to the dry-run channel; no network, no SMTP."""
    channel = "dryrun"

    def send(self, to, subject, body, body_html=None):
        return None


class FakeMailer(Mailer):
    """Tests: records messages; `fail_for` = addresses (or '*') that raise; `fail_times` = fail the first N attempts per address."""
    channel = "fake"

    def __init__(self, fail_for=(), fail_times=None, delay_s: float = 0.0):
        self.sent, self.attempts, self.fail_for, self.fail_times, self.delay_s = [], [], set(fail_for), fail_times, delay_s
        self._lock = threading.Lock()

    def send(self, to, subject, body, body_html=None):
        import time
        if self.delay_s:
            time.sleep(self.delay_s)
        with self._lock:
            self.attempts.append((to, subject))
            n = sum(1 for a, _ in self.attempts if a == to)
            if "*" in self.fail_for or to in self.fail_for:
                if self.fail_times is None or n <= self.fail_times:
                    raise ConnectionError("fake SMTP failure")
            self.sent.append({"to": to, "subject": subject, "body": body, "html": body_html, "t": time.time()})


class SMTPMailer(Mailer):
    channel = "smtp"

    def __init__(self, host=None, port=None, user=None, password=None, timeout_s: float = 20.0):
        load_env()
        self.host, self.user, self.password = host or os.environ.get("ECHOS_SMTP_HOST", ""), user or os.environ.get("ECHOS_SMTP_USER", ""), \
            password or os.environ.get("ECHOS_SMTP_PASSWORD", "")
        self.port, self.timeout_s = int(port or os.environ.get("ECHOS_SMTP_PORT", "587") or 587), timeout_s

    def configured(self) -> bool:
        return bool(self.host and self.user and self.password)

    def send(self, to, subject, body, body_html=None):
        if not self.configured():
            raise RuntimeError("SMTP not configured (ECHOS_SMTP_HOST/USER/PASSWORD)")
        m = EmailMessage()
        m["From"], m["To"], m["Subject"] = self.user, to, subject
        m.set_content(body)
        if body_html:
            m.add_alternative(body_html, subtype="html")
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout_s) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(self.user, self.password)
            s.send_message(m)


def make_mailer(cfg: dict | None = None) -> Mailer:
    a = (cfg or load_config()).get("alerts", {})
    return SMTPMailer() if a.get("send_real", False) else DryRunMailer()
