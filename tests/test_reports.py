import json

import pytest

from echos.orchestrator import state as S
from echos.reports.daily import agent as A
from echos.reports.llm.fake_llm import FakeLLM
from echos.reports.llm.llm import make_llm
from echos.reports.daily.validator import validate_report
from echos.reports.retrieval import GatedRetriever

NO_RAG = lambda: GatedRetriever(lambda *a, **k: [], pubmed=None)      # noqa: E731  (no outbound PubMed call in tests)


def facts(day="2019-05-01", status="MONITOR", sys_now=162.0, with_flag=True):
    v = {k: {"value": None, "baseline": None, "z": None} for k in ("sys", "dia", "hr", "temp", "weight")}
    v["sys"] = {"value": sys_now, "baseline": 128.0, "z": 2.1}
    flags = [{"id": "F1", "type": "blood_pressure", "evidence": {"kind": "vitals_alert", "risk": 0.71, "sys_now": sys_now,
                                                                 "sys_baseline": 128.0}}] if with_flag else []
    return {"patient_id": "p1", "day": day, "status": status, "data_gap": False, "triage_reasons": [], "has_reading": True,
            "vitals": v,
            "falls": [], "flags": flags}


@pytest.fixture
def agent_env(tmp_path):
    return S.get_engine(tmp_path / "t.db"), tmp_path


def good(sys_text="162"):
    return json.dumps({"status": "MONITOR", "assessment": [{"text": f"Systolic pressure was {sys_text} against usual 128.", "cites": ["F1"]}],
                       "changes": [{"text": f"Systolic pressure was {sys_text} against usual 128.", "cites": ["F1"]}],
                       "actions": [{"priority": "TODAY", "owner": "CAREGIVER", "text": "Repeat the reading later today.", "escalate_if": "", "cites": ["F1"]}], "guidance": []})


def script(*texts):
    """the final answer for each text (the patient context is in the first prompt; no tool call)"""
    return [{"text": t} for t in texts]


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


def test_llm_off():
    assert make_llm({"llm": {"model": None}}) is None


def test_llm_valid_retry_and_fallback(agent_env):
    eng, tmp = agent_env
    ok = A.ReportAgent(eng, FakeLLM(script(good())), root=tmp, retriever=NO_RAG()).daily(facts())
    assert ok["mode"] == "llm" and ok["provenance"]["model"] == "fake-model"
    llm = FakeLLM(script(good("171"), good()))            # first reply has a wrong number -> retried once with the validator's errors
    assert A.ReportAgent(eng, llm, root=tmp, retriever=NO_RAG()).daily(facts())["mode"] == "llm" and llm.calls == 2
    llm = FakeLLM(script(good("171"), good("171"), good()))  # two bad replies -> template, no third request
    out = A.ReportAgent(eng, llm, root=tmp, retriever=NO_RAG()).daily(facts())
    assert out["mode"] == "template" and llm.calls == 2 and out["validation"]["fallback_reason"].startswith("fake-model: validation_failed")


def test_deferred_llm_never_blocks_the_caller(agent_env):
    import time
    eng, tmp = agent_env

    def slow(system, messages, tools):
        time.sleep(1.0)
        return {"text": good()}
    ag = A.ReportAgent(eng, FakeLLM([slow]), root=tmp, retriever=NO_RAG())
    t0 = time.time()
    out = ag.daily(facts(), defer_llm=True)
    assert time.time() - t0 < 0.5 and out["mode"] == "template"  # template written immediately
    ag.drain(10)
    with eng.begin() as c:
        assert c.execute(S.reports.select()).first().mode == "llm"  # upgraded in the background


def test_rate_limiter_spaces_requests():
    import time
    from echos.reports.llm.llm import RateLimiter
    rl, t0 = RateLimiter(600), time.monotonic()  # 0.1 s gap
    for _ in range(4):
        rl.wait()
    assert time.monotonic() - t0 >= 0.29


def test_watchdog_enforces_short_timeout():
    import time
    from echos.reports.llm.llm import GeminiLLM, LLMTimeout
    g = GeminiLLM.__new__(GeminiLLM)  # no network: a hung call
    g.timeout_s = 0.3
    t0 = time.time()
    with pytest.raises(LLMTimeout):
        g._guard(lambda: time.sleep(3))
    assert time.time() - t0 < 1.5


def test_api_error_carries_http_code_and_status():
    from echos.reports.llm import llm as L

    class E(Exception):
        code, status, message, details = 429, "RESOURCE_EXHAUSTED", "quota exceeded, retry in 3s", None
    g = L.GeminiLLM.__new__(L.GeminiLLM)
    g.timeout_s = 5

    def boom():
        raise E("x")
    with pytest.raises(L.LLMAPIError) as ei:
        g._guard(boom)
    assert ei.value.code == 429 and ei.value.status == "RESOURCE_EXHAUSTED" and L._retry_delay(E("x")) == 3.0


def _weekly_with_fake_rag(agent_env, monkeypatch, fake, llm=None):
    eng, tmp = agent_env
    monkeypatch.setattr(A, "retrieve", fake)
    ag = A.ReportAgent(eng, llm, root=tmp, retriever=NO_RAG())
    for i in range(1, 8):
        ag.daily(facts(day=f"2019-05-0{i}", sys_now=150.0 + i, status="MONITOR", with_flag=True))
    return ag.weekly("p1", "2019-05-07")


def _chunk(ft, d=None, k=3):
    return [{"id": "KB-7", "text": "Check blood pressure again later and review medicines.", "title": "Hypertension in adults: diagnosis and management",
             "source": "NICE NG136 (2019)", "doc_id": "NICE-NG136", "page": "5", "score": 0.6, "finding_type": ft}]


def test_weekly_references_section_and_rag_off(agent_env, monkeypatch):
    out = _weekly_with_fake_rag(agent_env, monkeypatch, _chunk)
    html = out["path"].read_text()
    assert out["mode"] == "template" and out["validation"]["ok"], out["validation"]
    assert "References (for clinician review)" in html and "KB-7" in html and "NICE NG136 (2019)" in html
    out = _weekly_with_fake_rag(agent_env, monkeypatch, lambda ft, d=None, k=3: [])  # RAG disabled / not ingested
    assert out["validation"]["ok"] and "No guideline sources used" in out["path"].read_text()


def test_weekly_llm_uncited_guidance_falls_back(agent_env, monkeypatch):
    class L:
        def generate(self, system, user):
            return json.dumps({"highlights": [{"text": "Alerts occurred.", "cites": ["E1"]}],
                               "guidance": [{"text": "Recheck blood pressure and review medicines.", "cites": []}]})
    out = _weekly_with_fake_rag(agent_env, monkeypatch, _chunk, llm=L())
    assert out["mode"] == "template" and out["validation"]["ok"]
