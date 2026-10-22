import smtplib
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _never_real_smtp(monkeypatch):
    """Tests must NEVER send real email: any attempt to open an SMTP connection fails the test."""
    def boom(*a, **k):
        raise AssertionError("a test tried to open a real SMTP connection")
    monkeypatch.setattr(smtplib, "SMTP", boom)
    monkeypatch.setattr(smtplib, "SMTP_SSL", boom)


@pytest.fixture(autouse=True)
def _test_recipients(monkeypatch):
    """.env is not loaded in tests; alerts go to FakeMailer / DryRun with example.test addresses only."""
    monkeypatch.setenv("ECHOS_CAREGIVER_EMAIL", "caregiver@example.test")
    monkeypatch.setenv("ECHOS_CLINICIAN_EMAIL", "clinician@example.test")
    try:
        import dotenv
        monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    except ImportError:
        pass


# ---- data that is not in a fresh clone (git-ignored: TIHM files, trained v2 models)
ROOT = Path(__file__).resolve().parent.parent
MISSING = [name for name, p in {"TIHM dataset (data/tihm)": ROOT / "data/tihm/Dataset/Physiology.csv",
                                "v2 models (scripts/train_v2.py)": ROOT / "models/v2/meta.json"}.items() if not p.exists()]


def _needs_missing_data(ex: BaseException) -> bool:
    msg = f"{type(ex).__name__}: {ex}"
    return isinstance(ex, FileNotFoundError) and any(k in msg for k in ("data/", "models/"))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """In a fresh clone (no data, no built tables or models) a test that needs them is reported as SKIPPED with the reason, not as a failure.
    Only when one of the data paths above is actually missing, and only for the missing-file kinds of error; every other failure stays a failure."""
    rep = (yield).get_result()
    if rep.failed and call.excinfo is not None and MISSING and _needs_missing_data(call.excinfo.value):
        rep.outcome = "skipped"
        rep.longrepr = (str(item.fspath), item.location[1] or 0, "Skipped: needs data not in a fresh clone (" + ", ".join(MISSING) + "); see README 'Reproduce'")
