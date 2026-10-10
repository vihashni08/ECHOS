import json

import pytest

from echos.orchestrator import state as S
from echos.reports import agent as A
from echos.reports.knowledge import finding_types, get_guideline
from echos.reports.llm import make_llm
from echos.reports.validator import validate_report


def facts(day="2019-05-01", status="MONITOR", sys_now=162.0, with_flag=True):
    v = {k: {"value": None, "baseline": None, "z": None} for k in ("sys", "dia", "hr", "temp", "weight")}
    v["sys"] = {"value": sys_now, "baseline": 128.0, "z": 2.1}
    flags = [{"id": "F1", "type": "blood_pressure", "evidence": {"kind": "vitals_alert", "risk": 0.71, "sys_now": sys_now,
                                                                 "sys_baseline": 128.0}}] if with_flag else []
    return {"patient_id": "p1", "day": day, "status": status, "data_gap": False, "triage_reasons": [], "has_reading": True,
            "has_activity": True, "vitals": v,
            "routine": {k: {"value": 10, "baseline": 9.0} for k in ("total_events", "night_events", "night_bathroom")},
            "falls": [], "flags": flags}


class FakeLLM:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), 0

    def generate(self, system, user):
        self.calls += 1
        return self.replies.pop(0)


@pytest.fixture
def agent_env(tmp_path):
    return S.get_engine(tmp_path / "t.db"), tmp_path


def good(sys_text="162"):
    return json.dumps({"changes": [{"text": f"Systolic pressure was {sys_text} against usual 128.", "cites": ["F1"]}],
                       "monitor": [{"text": "Repeat the reading later today.", "cites": ["F1"]},
                                   {"text": "Keep drinking fluids.", "cites": []}]})


def test_validator_rejects_wrong_number():
    payload = {"facts": facts()}
    ok = {"changes": [{"text": "Systolic was 162 against usual 128.", "cites": ["F1"]}]}
    bad = {"changes": [{"text": "Systolic was 171 against usual 128.", "cites": ["F1"]}]}
    assert validate_report(ok, payload, ["F1"], ["F1"])["ok"]
    r = validate_report(bad, payload, ["F1"], ["F1"])
    assert not r["ok"] and any("171" in e for e in r["errors"])


def test_validator_rejects_diagnosis_and_uncited_flag():
    payload = {"facts": facts()}
    assert not validate_report({"changes": [{"text": "This is a diagnosis of hypertension.", "cites": ["F1"]}]}, payload, ["F1"], ["F1"])["ok"]
    assert not validate_report({"changes": [{"text": "You have high blood pressure.", "cites": ["F1"]}]}, payload, ["F1"], ["F1"])["ok"]
    r = validate_report({"changes": [{"text": "Pressure was higher than usual.", "cites": []}]}, payload, ["F1"], ["F1"], ["changes"])
    assert not r["ok"]
    assert not validate_report({"changes": [{"text": "Higher.", "cites": ["F9"]}]}, payload, [], ["F1"])["ok"]


def test_template_fallback_with_llm_off(agent_env):
    eng, tmp = agent_env
    ag = A.ReportAgent(eng, llm=None, root=tmp)
    out = ag.daily(facts())
    assert out["mode"] == "template" and out["validation"]["ok"]
    assert "Changes noticed" in out["text"] and "162" in out["text"]
    normal = ag.daily(facts(day="2019-05-02", status="NORMAL", with_flag=False))
    assert "normal" in normal["text"].lower() and normal["validation"]["ok"]


def test_llm_valid_retry_and_fallback(agent_env):
    eng, tmp = agent_env
    ok = A.ReportAgent(eng, FakeLLM(good()), root=tmp).daily(facts())
    assert ok["mode"] == "llm"
    llm = FakeLLM(good("171"), good())            # first reply has a wrong number -> retried once
    assert A.ReportAgent(eng, llm, root=tmp).daily(facts())["mode"] == "llm" and llm.calls == 2
    llm = FakeLLM(good("171"), good("171"), good())  # two bad replies -> template, no third call
    out = A.ReportAgent(eng, llm, root=tmp).daily(facts())
    assert out["mode"] == "template" and llm.calls == 2 and out["validation"]["notes"]
    out = A.ReportAgent(eng, FakeLLM("not json", "still not"), root=tmp).daily(facts())
    assert out["mode"] == "template"


def test_weekly_html_and_tools(agent_env):
    eng, tmp = agent_env
    ag = A.ReportAgent(eng, None, root=tmp)
    for i in range(1, 8):
        ag.daily(facts(day=f"2019-05-0{i}", sys_now=150.0 + i, status="MONITOR" if i % 2 else "NORMAL", with_flag=bool(i % 2)))
    assert len(ag.tools.get_last_7_days("p1", "2019-05-07")) == 7
    out = ag.weekly("p1", "2019-05-07")
    html = out["path"].read_text()
    assert out["mode"] == "template" and out["validation"]["ok"], out["validation"]
    assert "for clinician review" in html and "data:image/png;base64" in html and "E1" in html


def test_knowledge_and_llm_off():
    assert len(sum((get_guideline(t) for t in finding_types()), [])) >= 10
    assert get_guideline("nope") == [] and all(g["note"] == "for clinician review" for g in get_guideline("fall"))
    assert make_llm({"llm": {"model": None}}) is None


class RaisingLLM:
    def __init__(self, exc):
        self.exc, self.calls = exc, 0

    def generate(self, system, user):
        self.calls += 1
        raise self.exc


def test_timeout_is_not_retried_and_fallback_has_reason(agent_env):
    eng, tmp = agent_env
    llm = RaisingLLM(TimeoutError("slow"))
    out = A.ReportAgent(eng, llm, root=tmp).daily(facts())
    assert out["mode"] == "template" and llm.calls == 1 and out["validation"]["fallback_reason"].startswith("timeout")


def test_other_errors_retry_at_most_once(agent_env):
    eng, tmp = agent_env
    llm = RaisingLLM(RuntimeError("boom"))
    out = A.ReportAgent(eng, llm, root=tmp).daily(facts())
    assert out["mode"] == "template" and llm.calls == 2 and out["validation"]["fallback_reason"].startswith("error")


def test_deferred_llm_never_blocks_the_caller(agent_env):
    import time
    eng, tmp = agent_env

    class Slow:
        def generate(self, system, user):
            time.sleep(1.0)
            return good()
    ag = A.ReportAgent(eng, Slow(), root=tmp)
    t0 = time.time()
    out = ag.daily(facts(), defer_llm=True)
    assert time.time() - t0 < 0.5 and out["mode"] == "template"  # template written immediately
    ag.drain(10)
    with eng.begin() as c:
        assert c.execute(S.reports.select()).first().mode == "llm"  # upgraded in the background


def test_rate_limiter_spaces_requests():
    import time
    from echos.reports.llm import RateLimiter
    rl, t0 = RateLimiter(600), time.monotonic()  # 0.1 s gap
    for _ in range(4):
        rl.wait()
    assert time.monotonic() - t0 >= 0.29


def test_watchdog_enforces_short_timeout_without_retry(agent_env):
    import time
    from echos.reports.llm import GeminiLLM
    eng, tmp = agent_env
    g = GeminiLLM.__new__(GeminiLLM)  # no network: stub the SDK call with a hang
    g.timeout_s, g.calls = 0.3, 0
    from echos.reports.llm import RateLimiter
    g.limiter = RateLimiter(0)

    def hang(system, user):
        g.calls += 1
        time.sleep(3)
    g._call = hang
    t0 = time.time()
    out = A.ReportAgent(eng, g, root=tmp).daily(facts())
    assert time.time() - t0 < 1.5 and g.calls == 1 and out["validation"]["fallback_reason"].startswith("timeout")
