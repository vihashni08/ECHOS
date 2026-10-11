import os
import smtplib
import socket

import pytest

os.environ.setdefault("MPLBACKEND", "Agg")  # report charts are drawn from a background thread: never use a GUI backend

BLOCKED_HOSTS = ("generativelanguage.googleapis.com", "eutils.ncbi.nlm.nih.gov")
ATTEMPTS: list = []


@pytest.fixture(autouse=True)
def _never_real_smtp(monkeypatch):
    """Tests must NEVER send real email: any attempt to open an SMTP connection fails the test."""
    def boom(*a, **k):
        raise AssertionError("a test tried to open a real SMTP connection")
    monkeypatch.setattr(smtplib, "SMTP", boom)
    monkeypatch.setattr(smtplib, "SMTP_SSL", boom)


@pytest.fixture(autouse=True)
def _no_real_llm_or_pubmed(monkeypatch):
    """Tests use FakeLLM and mocked PubMed only. Any outbound connection to Gemini or NCBI E-utilities is blocked, recorded and fails the test
    at teardown even if the code under test swallows the error. The API key is removed from the environment (and .env is not loaded)."""
    ATTEMPTS.clear()
    real_gai, real_cc = socket.getaddrinfo, socket.create_connection

    def is_blocked(host) -> bool:
        return any(str(host).lower().rstrip(".").endswith(h) for h in BLOCKED_HOSTS)

    def gai(host, *a, **k):
        if is_blocked(host):
            ATTEMPTS.append(str(host))
            raise OSError(f"blocked by tests/conftest.py: outbound HTTP to {host}")
        return real_gai(host, *a, **k)

    def cc(address, *a, **k):
        if is_blocked(address[0]):
            ATTEMPTS.append(str(address[0]))
            raise OSError(f"blocked by tests/conftest.py: outbound connection to {address[0]}")
        return real_cc(address, *a, **k)
    monkeypatch.setattr(socket, "getaddrinfo", gai)
    monkeypatch.setattr(socket, "create_connection", cc)
    monkeypatch.delenv("ECHOS_LLM_API_KEY", raising=False)
    monkeypatch.delenv("NCBI_API_KEY", raising=False)
    monkeypatch.setenv("ECHOS_CAREGIVER_EMAIL", "caregiver@example.test")    # .env is not loaded in tests; alerts are DryRun / FakeMailer only
    monkeypatch.setenv("ECHOS_CLINICIAN_EMAIL", "clinician@example.test")
    try:
        import dotenv
        monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    except ImportError:
        pass
    yield
    assert not ATTEMPTS, f"a test tried outbound HTTP to {sorted(set(ATTEMPTS))} (use FakeLLM / mocked PubMed)"
