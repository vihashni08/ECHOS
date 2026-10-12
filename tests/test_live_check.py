"""scripts/live_llm_check.py logic with FAKE clients (no network, no key use): check order, request cap, WARN / FAIL rules."""
import sys

import pytest

from echos.reports import llm as L
from tests.test_weekly import world  # noqa: F401  (fixture)
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


# ---------------------------------------------------------------- scripts/live_report_check.py (fakes only: no network, no key use)
def test_live_report_check_refuses_without_yes_and_when_offline(monkeypatch, capsys):
    from scripts import live_report_check as LR
    assert LR.main([]) == 2 and "Re-run with --yes" in capsys.readouterr().out
    monkeypatch.setenv("ECHOS_REPORT_LLM", "off")
    assert LR.main(["--yes"]) == 2
    monkeypatch.delenv("ECHOS_REPORT_LLM")
    monkeypatch.setenv("ECHOS_PUBMED", "off")
    assert LR.main(["--yes"]) == 2


def test_live_report_check_caps_and_stops_at_first_primary_failure():
    from scripts import live_report_check as LR

    class C:
        model = "m"
        calls = 0

        def __init__(self, err=None):
            self.err = err

        def step(self, *a, **k):
            C.calls += 1
            if self.err:
                raise self.err
            return "ok"
    run = LR.Run(max_gemini=3)
    ok = LR.CappedClient(C(), run, primary=True)
    assert [ok.step("s", []) for _ in range(3)] == ["ok"] * 3
    with pytest.raises(LR.CapReached):
        ok.step("s", [])
    assert C.calls == 3 and run.gemini == 3                                    # the 4th request was never sent
    run2, C.calls = LR.Run(), 0
    bad = LR.CappedClient(C(L.LLMAPIError(503, "UNAVAILABLE", "x")), run2, primary=True)
    with pytest.raises(L.LLMAPIError):
        bad.step("s", [])
    assert "primary model failed" in run2.stop_reason
    with pytest.raises(LR.CapReached):                                       # after a stop nothing more is sent, not even to the fallback
        LR.CappedClient(C(), run2, primary=False).step("s", [])
    assert C.calls == 1
    run3 = LR.Run()
    with pytest.raises(L.LLMAPIError):
        LR.CappedClient(C(L.LLMAPIError(404, "NOT_FOUND", "x")), run3, primary=False).step("s", [])
    assert "404" in run3.stop_reason                                          # a 404 stops the run even on the fallback model


def test_live_report_check_pubmed_cap():
    from scripts import live_report_check as LR
    run, sent = LR.Run(max_pubmed=2), []
    get = LR.capped_pubmed_get(run, lambda u, p, t: sent.append(u) or b"<x/>")
    get("u", {}, 1), get("u", {}, 1)
    with pytest.raises(LR.CapReached):
        get("u", {}, 1)
    assert len(sent) == 2 and run.pubmed == 2


def test_live_report_check_end_to_end_with_fake_clients(world, tmp_path, monkeypatch, capsys):    # noqa: F811
    """Whole path with a FakeLLM-style client and mocked PubMed: caps respected, table printed, report text hidden unless --show, nothing mailed."""
    from echos.knowledge.pubmed import PubMedClient
    from echos.reports.fake_llm import FakeLLM
    from echos.reports.llm_budget import Backend, Cache, LLMBudget
    from scripts import live_report_check as LR
    eng, _, kw = world
    monkeypatch.setattr(LR, "pick_items", lambda db: {})        # nothing to pick -> every row 'not run', no model call at all
    run = LR.Run()
    fake = FakeLLM([])
    budget = LLMBudget([Backend("fake", LR.CappedClient(fake, run, True))], Cache(":memory:"))
    pm = PubMedClient(http_get=LR.capped_pubmed_get(run, lambda *a: b"<x/>"), cache_path=tmp_path / "pm.sqlite")
    db = tmp_path / "x.db"
    from echos.orchestrator import state as S
    S.get_engine(db)
    rows = LR.run_check(db, run, budget, pm)
    LR.print_table(rows, run)
    out = capsys.readouterr().out
    assert all(r[1].startswith("not run") for r in rows) and "real Gemini requests: 0 (cap 8); real PubMed requests: 0 (cap 6)" in out


def test_live_report_check_picks_a_monitor_day_an_urgent_fall_day_and_a_week(world):    # noqa: F811
    from scripts import live_report_check as LR
    eng, tmp, _ = world
    p = LR.pick_items(tmp / "w.db")
    assert p["monitor"][1] == "2019-05-07" and p["urgent"][1] == "2019-05-09" and p["urgent"][2]["falls"] and p["week"] == ("pw", "2019-W19")
