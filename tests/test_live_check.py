"""scripts/live_llm_check.py logic with FAKE clients (no network, no key use): check order, request cap, WARN / FAIL rules."""
import sys

import pytest

from echos.reports import llm as L
from scripts import live_llm_check as LC


def make_fake(behaviour):
    """behaviour: {model: error or None}. Records every request in `log`."""
    log = []

    class Fake:
        def __init__(self, key, model, *a, **k):
            self.model = model

        def step(self, system, messages, tools=None):
            log.append((self.model, bool(tools), len(messages)))
            err = behaviour.get(self.model)
            if err == "timeout":
                raise L.LLMTimeout("no response within 45s")
            if err:
                raise L.LLMAPIError(err, "ERR", "fake")
            if tools:
                return L.Step(text="", calls=[{"name": "get_number", "args": {}}], raw=None)
            txt = "7" if len(messages) > 1 else '{"ok": true}'
            return L.Step(text=txt, calls=[], raw=None)
    return Fake, log


@pytest.fixture
def run(monkeypatch, capsys):
    monkeypatch.delenv("ECHOS_REPORT_LLM", raising=False)
    monkeypatch.setenv("ECHOS_LLM_API_KEY", "AIza" + "x" * 35)
    monkeypatch.setattr(LC, "ROOT", LC.ROOT)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)

    def go(behaviour, *extra):
        fake, log = make_fake(behaviour)
        monkeypatch.setattr(L, "GeminiLLM", fake)
        LC.Capped.used, LC.Capped.cap = 0, 4
        monkeypatch.setattr(sys, "argv", ["x", "--yes", "--skip-list-check", *extra])
        rc = LC.main()
        return rc, log, capsys.readouterr().out
    return go


def models():
    from echos.config import load_config
    c = load_config()["llm"]
    return c["model"], c["fallback_model"]


def test_order_is_primary_plain_primary_tools_fallback_plain(run):
    p, f = models()
    rc, log, out = run({})
    assert rc == 0 and log == [(p, False, 1), (p, True, 1), (p, False, 3), (f, False, 1)]       # 4 requests: plain, tool round trip (2), fallback
    assert out.index("primary plain") < out.index("primary function calling") < out.index("fallback plain")


@pytest.mark.parametrize("err", [429, 503, "timeout"])
def test_transient_fallback_error_is_only_a_warning(run, err):
    p, f = models()
    rc, log, out = run({f: err})
    assert rc == 0 and "WARN: transient, not a configuration error" in out and "FAIL" not in out


def test_fallback_404_is_a_failure(run):
    p, f = models()
    rc, log, out = run({f: 404})
    assert rc == 1 and "FAIL" in out and "404" in out


@pytest.mark.parametrize("err", [404, 429, 503, "timeout"])
def test_primary_failure_stops_the_run_after_one_request(run, err):
    p, f = models()
    rc, log, out = run({p: err})
    assert rc == 1 and len(log) == 1 and "FAIL" in out and "WARN" not in out
