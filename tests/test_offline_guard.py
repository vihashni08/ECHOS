"""Entry-point audit: with the off-switches set, no Gemini / PubMed / SMTP client is created, even with a real-looking key in the environment."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FAKE_KEY = "AIzaSyA" + "x" * 32
ENTRY_POINTS = [
    "echos.reports.llm.make_llm", "echos.reports.llm.GeminiLLM", "echos.reports.llm_budget.make_budget", "echos.knowledge.pubmed.PubMedClient (default http)",
    "echos.alerts.mailer.make_mailer", "echos.alerts.mailer.SMTPMailer.send", "echos.orchestrator.pipeline.build_pipeline(report_llm='auto')",
    "echos.sim.controller (--no-llm / --no-pubmed)", "echos.api.app (ECHOS_REPORT_LLM / ECHOS_DRY_RUN via run_api.sh)",
    "scripts/run_api.sh (--dry --no-llm --no-pubmed)", "web/playwright.config.ts (run_api.sh --dry --no-llm)",
    "scripts/demo/run_demo.py, e2e.py, fault_injection.py, kill_test.py, alert_latency.py (force_offline)",
    "scripts/realservices/live_llm_check.py, llm_smoke.py, make_samples.py, list_models.py (--yes / off-switch refusal)",
    "scripts/realservices/send_test_email.py, send_test_weekly.py (--yes; SMTPMailer refuses under ECHOS_DRY_RUN=1)",
]


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setenv("ECHOS_LLM_API_KEY", FAKE_KEY)
    monkeypatch.setenv("ECHOS_SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("ECHOS_SMTP_USER", "u@example.test")
    monkeypatch.setenv("ECHOS_SMTP_PASSWORD", "x")
    created = []
    from google import genai
    monkeypatch.setattr(genai, "Client", lambda *a, **k: created.append("genai") or (_ for _ in ()).throw(AssertionError("genai.Client created")))
    import urllib.request as ur
    monkeypatch.setattr(ur, "urlopen", lambda *a, **k: created.append("urlopen") or (_ for _ in ()).throw(AssertionError("urlopen called")))
    return created


def test_switches_off_create_no_client(armed, monkeypatch, tmp_path):
    monkeypatch.setenv("ECHOS_REPORT_LLM", "off")
    monkeypatch.setenv("ECHOS_PUBMED", "off")
    monkeypatch.setenv("ECHOS_DRY_RUN", "1")
    from echos.alerts.mailer import DryRunMailer, SMTPMailer, make_mailer
    from echos.knowledge.pubmed import PubMedClient, PubMedError
    from echos.reports.llm import GeminiLLM, make_llm
    from echos.reports.llm_budget import make_budget
    assert make_llm() is None and make_budget() is None
    with pytest.raises(RuntimeError):
        GeminiLLM(FAKE_KEY, "m")
    c = PubMedClient(cache_path=tmp_path / "pm.sqlite")
    with pytest.raises(PubMedError, match="disabled"):
        c.search("blood_pressure", {})
    from echos.knowledge import pubmed
    with pytest.raises(PubMedError):
        pubmed._http_get("https://eutils.ncbi.nlm.nih.gov/x", {}, 1)     # the default transport itself refuses
    assert isinstance(make_mailer({"alerts": {"send_real": True}}), DryRunMailer)
    with pytest.raises(RuntimeError, match="ECHOS_DRY_RUN"):
        SMTPMailer().send("a@example.test", "s", "b")
    assert armed == []


def test_build_pipeline_auto_makes_no_llm(armed, monkeypatch, tmp_path):
    monkeypatch.setenv("ECHOS_REPORT_LLM", "off")
    from echos.orchestrator import state as S
    from echos.orchestrator.pipeline import build_pipeline
    build_pipeline(S.get_engine(tmp_path / "t.db"), report_llm="auto", report_root=tmp_path)
    assert armed == []


def test_send_real_false_by_default():
    from echos.alerts.mailer import DryRunMailer, make_mailer
    from echos.config import load_config
    assert load_config()["alerts"]["send_real"] is False
    assert isinstance(make_mailer(load_config()), DryRunMailer)


def test_script_wiring():
    for s in ("run_demo", "e2e", "fault_injection", "kill_test", "alert_latency", "fall_eval", "gate_eval"):
        assert "force_offline()" in next((ROOT / "scripts").glob(f"*/{s}.py")).read_text(), s
    for s in ("live_llm_check", "llm_smoke", "make_samples"):
        assert '"--yes"' in next((ROOT / "scripts").glob(f"*/{s}.py")).read_text(), s
    for s in ("send_test_email", "send_test_weekly"):
        assert "--yes" in next((ROOT / "scripts").glob(f"*/{s}.py")).read_text(), s
    sh = (ROOT / "scripts" / "run_api.sh").read_text()
    assert "--no-pubmed" in sh and "ECHOS_REPORT_LLM=off ECHOS_PUBMED=off" in sh
    assert "--dry --no-llm" in (ROOT / "web" / "playwright.config.ts").read_text()
    assert "ECHOS_REPORT_LLM" in (ROOT / "echos" / "sim" / "controller.py").read_text()


def test_importing_script_modules_does_not_change_the_environment():
    import importlib
    import os
    before = {k: os.environ.get(k) for k in ("ECHOS_REPORT_LLM", "ECHOS_PUBMED", "ECHOS_DRY_RUN")}
    for m in ("scripts.evaluation.alert_latency", "scripts.evaluation.kill_test"):
        importlib.import_module(m)
    assert {k: os.environ.get(k) for k in before} == before
