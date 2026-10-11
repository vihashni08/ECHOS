import os
import smtplib

import pytest

os.environ.setdefault("MPLBACKEND", "Agg")  # report charts are drawn from a background thread: never use a GUI backend


@pytest.fixture(autouse=True)
def _never_real_smtp(monkeypatch):
    """Tests must NEVER send real email: any attempt to open an SMTP connection fails the test."""
    def boom(*a, **k):
        raise AssertionError("a test tried to open a real SMTP connection")
    monkeypatch.setattr(smtplib, "SMTP", boom)
    monkeypatch.setattr(smtplib, "SMTP_SSL", boom)
