import os
import smtplib
import socket
from pathlib import Path

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


# ---- data that is not in a fresh clone (git-ignored: TIHM / SisFall / guideline PDFs / built tables)
ROOT = Path(__file__).resolve().parent.parent
MISSING = [name for name, p in {"built tables (data/processed)": ROOT / "data/processed/patient_day.parquet",
                                "SisFall recordings": ROOT / "data/sisfall/SisFall_dataset/SA01",
                                "guideline PDFs": ROOT / "echos/knowledge/raw"}.items() if not p.exists()]


def _needs_missing_data(ex: BaseException) -> bool:
    msg = f"{type(ex).__name__}: {ex}"
    return ((isinstance(ex, FileNotFoundError) and any(k in msg for k in ("data/", "models/", "knowledge/raw")))
            or (isinstance(ex, KeyError) and "subject" in msg) or (isinstance(ex, AttributeError) and "is_fall" in msg)
            or (isinstance(ex, AssertionError) and "training table has no" in msg))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """In a fresh clone (no data, no built tables or models) a test that needs them is reported as SKIPPED with the reason, not as a failure.
    Only when one of the data paths above is actually missing, and only for the missing-file kinds of error; every other failure stays a failure."""
    rep = (yield).get_result()
    if rep.failed and call.excinfo is not None and MISSING and _needs_missing_data(call.excinfo.value):
        rep.outcome = "skipped"
        rep.longrepr = (str(item.fspath), item.location[1] or 0, "Skipped: needs data not in a fresh clone (" + ", ".join(MISSING) + "); see README 'Reproduce'")
