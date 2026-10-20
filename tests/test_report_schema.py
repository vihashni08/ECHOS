"""Regression tests built from the two saved live replies (tests/fixtures/live/*.json: exact key names and types, placeholder text only)
plus the maximum number of model requests per scenario (FakeLLM, no network)."""
import json
import re
from pathlib import Path

import pytest

from echos.reports.daily import schema as SC
from echos.reports.llm.fake_llm import FakeLLM
from echos.reports.retrieval import GatedRetriever
from tests.test_report_llm import KB, LAST7, answer, env, make  # noqa: F401
from tests.test_reports import facts

FIX = Path(__file__).parent / "fixtures" / "live"


def fixture(name):
    d = json.loads((FIX / f"{name}.json").read_text())
    return [({"calls": LAST7["calls"]} if r["kind"] == "tool_call_empty_text" else {"text": r["text"]}) for r in d["requests"]]


def body(resp):
    return json.loads(re.sub(r"^```json\s*|```\s*$", "", resp["text"].strip()))


def test_fixtures_contain_no_patient_text():
    for n in ("monitor", "urgent"):
        raw = (FIX / f"{n}.json").read_text()
        assert "TIHM" not in raw and raw.count("Neutral placeholder sentence") >= 3


@pytest.mark.parametrize("name", ["monitor", "urgent"])
def test_live_shapes_are_rejected_with_a_schema_rule(name):
    _, first, second = fixture(name)
    m, errs = SC.parse(body(first))                 # the old shape (bare strings under "monitor"): no assessment / actions, and "monitor" is not a key any more
    assert m is None and any("monitor" in e for e in errs) and any(e.startswith("schema mismatch at assessment") for e in errs)
    m, errs = SC.parse(body(second))                # retry: a different schema altogether (patient_id/day/note[s])
    assert m is None and any("changes" in e for e in errs) and any("extra_forbidden" in e for e in errs)


def test_schema_is_strict_about_empty_text_missing_cites_and_counts():
    good = body(answer())
    assert SC.parse(good)[0] is not None
    act = lambda **o: {"priority": "TODAY", "owner": "CAREGIVER", "text": "x", "escalate_if": "", "cites": [], **o}      # noqa: E731
    assert SC.parse({**good, "actions": [act(text="  ")]})[0] is None
    assert SC.parse({**good, "actions": [{k: v for k, v in act().items() if k != "cites"}]})[0] is None
    assert SC.parse({**good, "actions": [act(priority="SOON")]})[0] is None and SC.parse({**good, "actions": [act(owner="NURSE")]})[0] is None
    assert SC.parse({**good, "actions": []})[0] is None and SC.parse({**good, "actions": [act()] * 6})[0] is None
    assert SC.parse({**good, "assessment": []})[0] is None
    assert SC.parse({**good, "guidance": [{"text": "g", "cites": []}] * 3})[0] is None


def test_prompt_and_gemini_schema_come_from_the_same_model(env):
    shape = SC.json_schema()
    assert set(shape["properties"]) == set(SC.DailyReport.model_fields) and shape["properties"]["actions"]["minItems"] == 1
    llm = FakeLLM([answer()])
    make(env, llm).daily(facts())
    assert json.dumps(SC.prompt_shape())[1:-1] in llm.prompts[0]


@pytest.mark.parametrize("name", ["monitor", "urgent"])
def test_saved_live_run_replayed_is_3_requests_and_a_schema_failure(env, name):
    llm = FakeLLM(fixture(name))                    # tool call, bare-string reply, wrong-schema retry
    out = make(env, llm).daily(facts())
    assert out["mode"] == "template" and llm.calls == 3 and out["provenance"]["requests"] == 3
    assert [r["tools"] is None for r in llm.requests] == [False, False, True]   # 1 = TOOL CALL (empty text), 2 = answer (tools still offered), 3 = the no-tools retry
    assert out["provenance"]["validator_codes"] == ["schema_mismatch"]


def test_a_corrected_live_reply_passes(env):
    out = make(env, FakeLLM([LAST7, fixture("monitor")[1], answer()])).daily(facts())
    assert out["mode"] == "llm" and out["provenance"]["requests"] == 3 and out["provenance"]["retried"]


def test_max_requests_per_scenario(env):
    ask = lambda ft: {"calls": [{"name": "retrieve_guidance", "args": {"finding_type": ft}}]}   # noqa: E731
    r = GatedRetriever(lambda ft, d=None: [KB], pubmed=None)
    bad = {"text": json.dumps({"patient_id": "Patient A", "note": "x"})}
    cases = [
        ("valid, no tool", [answer()], 1),
        ("one tool", [LAST7, answer()], 2),
        ("3 tools + final", [LAST7, ask("bp_high"), ask("hr_high"), answer()], 4),
        ("bad final + retry, no tool", [bad, answer()], 2),
        ("hostile model forever, no tool", [bad], 2),                                  # final + ONE retry, then the template
        ("hostile model forever, 3 tools", [LAST7, ask("bp_high"), ask("hr_high"), bad], 5),
        ("model asks for tools forever", [LAST7], 4),
        ("unparsable JSON forever", [{"text": "not json"}], 2),
    ]
    for i, (label, script, mx) in enumerate(cases):
        llm = FakeLLM(script)
        out = make(env, llm, retriever=r).daily(facts(day=f"2019-06-{i + 1:02d}"))
        assert llm.calls <= mx and out["provenance"]["requests"] == llm.calls, label
    # a failing primary adds its own requests: 503 + one same-model retry on the primary, then the fallback model
    p, f = FakeLLM([{"error": {"code": 503}}]), FakeLLM([answer()])
    make(env, p, f).daily(facts(day="2019-06-20"))
    assert p.calls == 2 and f.calls == 1
