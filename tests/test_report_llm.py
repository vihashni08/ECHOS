"""Daily report: budget manager (cache -> primary -> fallback -> template), tool loop, validator, gated retrieval, PubMed. FakeLLM and mocked
PubMed only; tests/conftest.py fails any test that attempts outbound HTTP to Gemini or NCBI."""
import json
import socket

import pytest

from echos.knowledge import pubmed as PM
from echos.orchestrator import state as S
from echos.reports import agent as A
from echos.reports.fake_llm import FakeLLM
from echos.reports.llm_budget import Backend, Cache, LLMBudget, SlidingLimiter, make_key
from echos.reports.retrieval import GatedRetriever
from echos.reports.validator import validate_report
from tests.test_reports import facts, good

CTX = {"calls": [{"name": "get_patient_context", "args": {}}]}
KB = {"id": "KB-7", "text": "Check blood pressure again later and review medicines.", "title": "Hypertension in adults", "source": "NICE NG136 (2019)",
      "doc_id": "NICE-NG136", "page": "5", "score": 0.6, "finding_type": "blood_pressure"}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def answer(**over):
    d = {"status": "MONITOR", "changes": [{"text": "Systolic pressure was 162 against usual 128.", "cites": ["F1"]}],
         "monitor": [{"text": "Repeat the reading later today.", "cites": ["F1"]}, {"text": "Keep drinking fluids.", "cites": []}], "guidance": []}
    d.update(over)
    return {"text": json.dumps(d)}


def ok_script(**over):
    return [CTX, answer(**over)]


@pytest.fixture
def env(tmp_path):
    return S.get_engine(tmp_path / "t.db"), tmp_path


def make(env, *llms, clock=None, retriever=None, cooldown=60.0):
    eng, tmp = env
    clock = clock or Clock()
    names = ["primary", "fallback"][:len(llms)]
    budget = LLMBudget([Backend(n, l) for n, l in zip(names, llms)], Cache(":memory:"), clock=clock, cooldown_s=cooldown)
    return A.ReportAgent(eng, None, root=tmp, budget=budget, retriever=retriever or GatedRetriever(lambda ft, d=None: [], pubmed=None))


def prov(env, day="2019-05-01"):
    with env[0].begin() as c:
        r = c.execute(S.reports.select().where(S.reports.c.day == day)).first()
    return json.loads(r.provenance)


# ------------------------------------------------------------------ fallback order, cooldown, retries
def test_fallback_order_primary_fallback_template(env):
    p, f = FakeLLM([{"error": {"code": 503, "status": "UNAVAILABLE"}}]), FakeLLM(ok_script())
    out = make(env, p, f).daily(facts())
    assert out["mode"] == "llm" and out["provenance"]["model"] == "fallback" and out["provenance"]["models_tried"] == ["primary", "fallback"]
    assert p.calls == 2 and f.calls == 2                      # 503: ONE retry on the same model, then the fallback model
    p, f = FakeLLM([{"error": {"code": 503}}]), FakeLLM([{"error": {"code": 500}}])
    out = make(env, p, f).daily(facts(day="2019-05-02"))
    assert out["mode"] == "template" and out["validation"]["ok"] and "primary: 503" in out["provenance"]["fallback_reason"]
    assert "fallback: 500" in out["provenance"]["fallback_reason"]


def test_429_starts_cooldown_and_moves_on_without_retry(env):
    clock = Clock()
    p, f = FakeLLM([{"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}}]), FakeLLM(ok_script())
    ag = make(env, p, f, clock=clock)
    out = ag.daily(facts(day="2019-05-01"))
    assert p.calls == 1 and out["provenance"]["model"] == "fallback"       # no retry on the same model
    assert any(e["event"] == "cooldown_start" and e["http_status"] == "429" for e in out["provenance"]["events"])
    ag.daily(facts(day="2019-05-02"))
    assert p.calls == 1                                                     # still cooling down: skipped, no request
    clock.t += 61
    ag.daily(facts(day="2019-05-03"))
    assert p.calls == 2                                                     # cooldown over: primary is tried again


def test_timeout_never_retried_and_other_errors_one_retry(env):
    p, f = FakeLLM([{"error": "timeout"}]), FakeLLM(ok_script())
    out = make(env, p, f).daily(facts())
    assert p.calls == 1 and out["provenance"]["model"] == "fallback"
    assert any(e["http_status"] == "timeout" for e in out["provenance"]["events"])
    p = FakeLLM([{"error": "boom"}, CTX, answer()])         # RuntimeError once, then fine on the same model
    out = make(env, p).daily(facts(day="2019-05-02"))
    assert out["mode"] == "llm" and out["provenance"]["model"] == "primary"
    assert any(e["event"] == "retry" for e in out["provenance"]["events"])


def test_bad_request_is_not_retried(env):
    p, f = FakeLLM([{"error": {"code": 400, "status": "INVALID_ARGUMENT"}}]), FakeLLM(ok_script())
    out = make(env, p, f).daily(facts())
    assert p.calls == 1 and out["provenance"]["model"] == "fallback"


def test_cache_hit_prevents_a_second_call(env):
    llm = FakeLLM(ok_script())
    ag = make(env, llm)
    first = ag.daily(facts())
    n = llm.calls
    second = ag.daily(facts())
    assert llm.calls == n and second["mode"] == "llm" and second["provenance"]["cache_hit"] and second["text"] == first["text"]
    assert prov(env)["cache_hit"] is True
    assert make_key("v", {"a": 1}) == make_key("v", {"a": 1}) != make_key("v2", {"a": 1})   # key = prompt version + structured input only


def test_cached_value_is_revalidated_and_failing_entry_is_ignored(env):
    llm = FakeLLM(ok_script())
    ag = make(env, llm)
    ag.daily(facts())
    key = make_key(A.PROMPT_VERSION, A.llm_view(facts()))
    entry = ag.budget.cache.get(key)
    entry.pop("_model")
    entry["items"]["changes"][0]["text"] = "Systolic was 171."
    ag.budget.cache.put(key, A.PROMPT_VERSION, "primary", entry)
    llm.script = ok_script()
    assert ag.daily(facts())["provenance"]["cache_hit"] is False and llm.calls == 4


def test_rate_limiter_stays_20_percent_below_the_limit():
    c = Clock()
    lim = SlidingLimiter(10, clock=c, sleep=c.sleep)          # limit 10/min -> 8 per 60 s
    for _ in range(8):
        assert lim.wait() == 0
    assert lim.wait() > 59 and SlidingLimiter(5).cap == 4 and SlidingLimiter(15).cap == 12 and SlidingLimiter(None).cap == 0


# ------------------------------------------------------------------ gate: NORMAL days
def test_normal_day_makes_zero_llm_and_zero_retrieval_calls(env):
    llm = FakeLLM(ok_script())
    pm = PM.PubMedClient(http_get=lambda *a: pytest.fail("PubMed called"), cache_path=env[1] / "pm.sqlite")
    r = GatedRetriever(lambda *a: pytest.fail("local retrieval called"), pubmed=pm)
    ag = make(env, llm, retriever=r)
    out = ag.daily(facts(status="NORMAL", with_flag=False), use_llm=False)          # what the orchestrator does for NORMAL days
    assert out["mode"] == "template" and llm.calls == 0 and r.calls == {"local": 0, "pubmed": 0} and pm.requests == 0
    # even if the gate were open, the retrieval tool refuses a NORMAL day
    llm = FakeLLM([CTX, {"calls": [{"name": "retrieve_guidance", "args": {"finding_type": "blood_pressure"}}]}, answer(status="NORMAL", changes=[
        {"text": "Everything looked normal.", "cites": []}])])
    ag = make(env, llm, retriever=r)
    ag.daily(facts(day="2019-05-02", status="NORMAL", with_flag=False))
    assert r.calls == {"local": 0, "pubmed": 0}


def test_orchestrator_gate_normal_days_never_reach_the_llm(tmp_path, monkeypatch):
    from tests.test_orchestrator import R, health, qual
    from tests.test_safety import _seed_case
    from echos.orchestrator.graph import build_graph, process_case
    from echos.reports.agent import drain_all
    monkeypatch.setattr(A.ReportAgent, "weekly", lambda *a, **k: None)   # 2019-05-05 is a Sunday; this test is about the DAILY report
    eng = S.get_engine(tmp_path / "g.db")
    _seed_case(eng, "2019-05-05", {"quality": qual(), "health_monitoring": health(), "fall": R(0.0)})
    llm = FakeLLM([{"error": {"code": 400}}])
    g, conn = build_graph(eng, tmp_path / "cp.sqlite", report_llm=llm, report_root=tmp_path, gate=True)
    process_case(g, "p1", "2019-05-05")
    drain_all(20)
    with eng.begin() as c:
        st = c.execute(S.case_state.select()).first().status
    conn.close()
    assert st == "NORMAL" and llm.calls == 0


# ------------------------------------------------------------------ tool loop
def test_tool_loop_stops_at_4_steps_then_final_answer_without_tools(env):
    llm = FakeLLM([CTX])                      # a model that asks for tools forever
    out = make(env, llm).daily(facts())
    assert out["mode"] == "template" and llm.calls == 5                        # 4 tool steps + 1 final request, no more
    assert all(r["tools"] for r in llm.requests[:4]) and llm.requests[4]["tools"] is None
    assert "tool steps" in out["provenance"]["fallback_reason"]


def test_tools_called_are_recorded_in_provenance(env):
    llm = FakeLLM([CTX, {"calls": [{"name": "get_last_7_days", "args": {}}]}, answer()])
    out = make(env, llm).daily(facts())
    assert out["mode"] == "llm" and [t["name"] for t in out["provenance"]["tools"]] == ["get_patient_context", "get_last_7_days"]
    assert out["provenance"]["tools"][0]["result_summary"].startswith("context for 2019-05-01")
    p = prov(env)
    assert p["requests"] == 3 and p["tokens"]["prompt"] == 300 and p["validator"]["ok"] and p["fallback_reason"] is None


def test_recorded_response_replay(env):
    out = make(env, FakeLLM.recorded("daily_monitor_bp")).daily(facts())
    assert out["mode"] == "llm" and "Status: MONITOR" in out["text"]


# ------------------------------------------------------------------ validator
PAY = {"facts": facts()}


def v(out, **kw):
    kw.setdefault("expected_status", "MONITOR")
    return validate_report(out, PAY, ["F1"], ["F1", "KB-7"], ["changes"], **kw)


def base(**over):
    d = json.loads(answer()["text"])
    d.update(over)
    return d


def test_validator_rejects_each_class_of_error():
    assert v(base())["ok"]
    assert any("171" in e for e in v(base(changes=[{"text": "Systolic was 171.", "cites": ["F1"]}]))["errors"])             # wrong number
    assert any("arbiter" in e for e in v(base(status="NORMAL"))["errors"])                                                  # status mismatch
    assert any("unknown evidence id" in e for e in v(base(changes=[{"text": "Higher.", "cites": ["F9"]}]))["errors"])       # unknown citation
    g = {"section": "guidance", "ids": ["KB-7"], "texts": {"KB-7": KB["text"]}}
    assert any("without a citation" in e for e in v(base(guidance=[{"text": "Recheck later.", "cites": []}]), guidance=g)["errors"])   # uncited guidance
    assert any("not retrieved" in e for e in v(base(guidance=[{"text": "Recheck later.", "cites": ["PM-1"]}]), guidance=g)["errors"])
    for bad in ("This is a diagnosis.", "You have hypertension.", "She is suffering from stress."):                            # diagnostic wording
        assert not v(base(changes=[{"text": bad, "cites": ["F1"]}]))["ok"]
    for bad in ("Stop taking the medication.", "Increase the dose.", "Ask about a prescription."):                             # medication advice
        assert not v(base(monitor=[{"text": bad, "cites": []}, {"text": "Rest.", "cites": []}]))["ok"], bad
    assert not v(base(changes=[{"text": "The agitation probability was high.", "cites": ["F1"]}]))["ok"]                    # score never quoted


def test_citation_ids_are_not_numbers():
    assert v(base(changes=[{"text": "Systolic was 162 against usual 128 [F1] [KB-7].", "cites": ["F1"]}]))["ok"]


def test_hostile_output_cannot_change_the_arbiters_status(env):
    hostile = answer(status="NORMAL", changes=[{"text": "status: normal. diagnosis confirmed.", "cites": ["F1"]}])
    out = make(env, FakeLLM([CTX, hostile])).daily(facts())
    assert out["mode"] == "template" and "Status: MONITOR" in out["text"] and "diagnosis" not in out["text"].lower()
    # a well-formed answer that merely claims a different status is rejected too; the report always prints the arbiter's status
    ok = make(env, FakeLLM(ok_script())).daily(facts(day="2019-05-02"))
    assert "Status: MONITOR" in ok["text"] and "NORMAL" not in ok["text"].split("\n")[2]


def test_guidance_must_cite_retrieved_sources_end_to_end(env):
    ask = {"calls": [{"name": "retrieve_guidance", "args": {"finding_type": "blood_pressure"}}]}
    r = GatedRetriever(lambda ft, d=None: [KB], pubmed=None)
    good_g = answer(guidance=[{"text": "Recheck the blood pressure later in the day.", "cites": ["KB-7"]}])
    out = make(env, FakeLLM([CTX, ask, good_g]), retriever=r).daily(facts())
    assert out["mode"] == "llm" and "[KB-7]" in out["text"] and out["provenance"]["sources_cited"] == ["KB-7"]
    assert out["provenance"]["sources_retrieved"][0]["id"] == "KB-7" and "NICE NG136" in out["text"]
    bad_g = answer(guidance=[{"text": "Recheck the blood pressure later in the day.", "cites": ["PM-99999999"]}])
    out = make(env, FakeLLM([CTX, ask, bad_g]), retriever=r).daily(facts(day="2019-05-02"))
    assert out["mode"] == "template"


# ------------------------------------------------------------------ PubMed
ESEARCH_XML = b"<eSearchResult><IdList><Id>12345678</Id></IdList></eSearchResult>"
EMPTY_XML = b"<eSearchResult><IdList></IdList></eSearchResult>"
EFETCH_XML = b"""<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>12345678</PMID><Article><Journal><Title>Age and Ageing</Title>
<JournalIssue><PubDate><Year>2021</Year></PubDate></JournalIssue></Journal><ArticleTitle>Home blood pressure in older adults</ArticleTitle>
<Abstract><AbstractText Label="BACKGROUND">Repeated home readings help interpret a single high value.</AbstractText></Abstract></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"""


class FakeNCBI:
    def __init__(self, filtered_empty=False, fail=None):
        self.calls, self.filtered_empty, self.fail = [], filtered_empty, fail

    def __call__(self, url, params, timeout):
        self.calls.append((url, dict(params), timeout))
        if self.fail:
            raise self.fail
        if "esearch" in url:
            return EMPTY_XML if self.filtered_empty and "[pt]" in params["term"] else ESEARCH_XML
        return EFETCH_XML


def client(tmp, http, clock=None):
    clock = clock or Clock()
    return PM.PubMedClient(http_get=http, cache_path=tmp / "pm.sqlite", clock=clock, sleep=clock.sleep)


def test_pubmed_query_filter_fallback_citation_cache_and_rate(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHOS_NCBI_EMAIL", "me@example.test")
    http, clock = FakeNCBI(filtered_empty=True), Clock()
    pm = client(tmp_path, http, clock)
    assert PM.build_query("fall_inactivity") == "older adults AND falls AND post-fall inactivity" and PM.build_query("data_gap") is None
    r = pm.search("fall_inactivity", {"post_fall_inactive": True})
    assert [u.split("/")[-1] for u, *_ in http.calls] == ["esearch.fcgi", "esearch.fcgi", "efetch.fcgi"]       # filtered search empty -> unfiltered -> fetch
    assert "[pt]" in http.calls[0][1]["term"] and "[pt]" not in http.calls[1][1]["term"] and not r["filtered"]
    assert http.calls[0][1]["tool"] == "echos" and http.calls[0][1]["email"] == "me@example.test" and http.calls[0][2] == 10 and "api_key" not in http.calls[0][1]
    s = r["sources"][0]
    assert s["id"] == "PM-12345678" and s["journal"] == "Age and Ageing" and s["year"] == "2021" and s["url"] == "https://pubmed.ncbi.nlm.nih.gov/12345678/"
    assert "Repeated home readings" in s["text"] and http.calls[0][1]["retmax"] == 3
    assert clock.t - 1000.0 >= 2 * (1 / 3) - 1e-6        # 3 requests need >= 2 gaps of 1/3 s (the fake clock only advances through the limiter's sleeps)
    n = len(http.calls)
    assert pm.search("fall_inactivity", {"post_fall_inactive": True})["cache_hit"] and len(http.calls) == n     # cached in SQLite


def test_pubmed_uses_review_filter_first(tmp_path):
    http = FakeNCBI()
    r = client(tmp_path, http).search("blood_pressure")
    assert r["filtered"] and len(http.calls) == 2 and r["requests"] == 2


def test_pubmed_failure_falls_back_to_local_sources(env, tmp_path):
    from urllib.error import HTTPError
    pm = client(tmp_path, FakeNCBI(fail=HTTPError("u", 429, "Too Many", {}, None)))
    r = GatedRetriever(lambda ft, d=None: [KB], pubmed=pm)
    got = r.retrieve("MONITOR", "blood_pressure")
    assert [s["id"] for s in got["sources"]] == ["KB-7"] and got["pubmed_failure"] == "HTTP 429"
    ask = {"calls": [{"name": "retrieve_guidance", "args": {"finding_type": "blood_pressure"}}]}
    g = answer(guidance=[{"text": "Recheck the blood pressure later in the day.", "cites": ["KB-7"]}])
    out = make(env, FakeLLM([CTX, ask, g]), retriever=r).daily(facts())
    p = out["provenance"]
    assert out["mode"] == "llm" and p["pubmed_failure"] == "HTTP 429" and any("pubmed failed (HTTP 429)" in e for e in p["retrieval_events"])


def test_pubmed_source_can_be_cited(env, tmp_path):
    r = GatedRetriever(lambda ft, d=None: [KB], pubmed=client(tmp_path, FakeNCBI()))
    ask = {"calls": [{"name": "retrieve_guidance", "args": {"finding_type": "blood_pressure"}}]}
    g = answer(guidance=[{"text": "Reviews describe repeating home readings to interpret one high value.", "cites": ["PM-12345678"]}])
    out = make(env, FakeLLM([CTX, ask, g]), retriever=r).daily(facts())
    assert out["mode"] == "llm" and "[PM-12345678] Home blood pressure in older adults - Age and Ageing, 2021 - https://pubmed.ncbi.nlm.nih.gov/12345678/" in out["text"]


def test_everything_external_down_still_gives_a_valid_report(env, tmp_path):
    pm = client(tmp_path, FakeNCBI(fail=OSError("network down")))

    def rag_down(ft, d=None):
        raise RuntimeError("RAG down")
    ask = {"calls": [{"name": "retrieve_guidance", "args": {"finding_type": "blood_pressure"}}]}
    ag = make(env, FakeLLM([{"error": {"code": 503}}]), FakeLLM([{"error": "timeout"}]), retriever=GatedRetriever(rag_down, pubmed=pm))
    out = ag.daily(facts())
    assert out["mode"] == "template" and out["validation"]["ok"] and "Status: MONITOR" in out["text"]
    # retrieval alone down while the LLM works: report is still produced, guidance empty
    out = make(env, FakeLLM([CTX, ask, answer()]), retriever=GatedRetriever(rag_down, pubmed=pm)).daily(facts(day="2019-05-02"))
    assert out["mode"] == "llm" and out["provenance"]["pubmed_failure"] == "OSError" and out["provenance"]["sources_retrieved"] == []


# ------------------------------------------------------------------ provenance, guard
def test_provenance_is_stored_for_template_reports_too(env):
    ag = make(env)
    ag.budget = None
    ag.daily(facts())
    p = prov(env)
    assert p["model"] == "template" and p["fallback_reason"] == "no LLM configured" and p["tools"] == [] and p["cache_hit"] is False


def test_outbound_calls_to_gemini_and_ncbi_are_blocked():
    from tests import conftest
    for host in conftest.BLOCKED_HOSTS:
        with pytest.raises(OSError):
            socket.getaddrinfo(host, 443)
    assert len(conftest.ATTEMPTS) == 2
    conftest.ATTEMPTS.clear()      # expected here; every other test must end with none
