"""Daily report v2: structured actions (priority / owner / escalation), the structured document + markdown, code-side retrieval, priority rules,
PubMed relevance. FakeLLM and mocked PubMed only."""
import json

from echos.knowledge import relevance
from echos.reports.daily import actions as ACT
from echos.reports.daily import agent as A
from echos.reports.daily import document as DOC
from echos.reports.daily.validator import validate_report
from echos.reports.llm.fake_llm import FakeLLM
from echos.reports.retrieval import GatedRetriever
from tests.test_report_llm import KB, FakeNCBI, answer, client, env, make  # noqa: F401
from tests.test_reports import facts


def bp_facts(**kw):
    f = facts(**kw)
    f["flags"][0]["type"] = "bp_high"
    f["flags"][0]["evidence"].update(values={"sys": 162.0, "dia": 90.0}, specifics=["Systolic blood pressure 162 mmHg is 34 mmHg above this person's usual of 128 mmHg."])
    return f


def fall_facts():
    f = facts(status="URGENT")
    f["flags"] = [{"id": "F1", "type": "fall_inactivity", "evidence": {"time": "10:18", "prob": 0.99, "peak_g": 4.2, "post_fall_inactive": True}}]
    return f


def test_a_fall_gets_a_right_now_action_with_escalation_and_a_clinician_follow_up():
    acts = ACT.build(fall_facts())
    assert [a["priority"] for a in acts] == ["NOW", "TODAY", "WITHIN_48H"] and [a["id"] for a in acts] == ["A1", "A2", "A3"]
    assert acts[0]["owner"] == "CAREGIVER" and "before lifting them" in acts[0]["text"] and "emergency" in acts[0]["escalate_if"]
    assert acts[2]["owner"] == "CLINICIAN"


def test_a_vitals_action_says_how_and_when_and_escalates_at_the_system_limits():
    th = ACT.thresholds()
    a = ACT.build(bp_facts())
    assert a[0]["priority"] == "TODAY" and a[0]["owner"] == "CAREGIVER" and "162/90" in a[0]["text"]
    assert str(th["sys_high"]) in a[0]["escalate_if"] and str(th["dia_high"]) in a[0]["escalate_if"]       # the limits come from config, never invented
    assert a[1]["owner"] == "CLINICIAN" and a[1]["priority"] == "WITHIN_48H"


def test_a_quiet_day_gets_one_routine_action():
    a = ACT.build(facts(status="NORMAL", with_flag=False))
    assert len(a) == 1 and a[0]["priority"] == "THIS_WEEK" and a[0]["escalate_if"] == ""


def test_template_report_is_a_structured_document_with_a_valid_plan(env):
    eng, tmp = env
    out = A.ReportAgent(eng, None, root=tmp).daily(bp_facts())
    assert out["validation"]["ok"], out["validation"]
    doc = json.loads(out["path"].with_suffix(".json").read_text())
    assert doc["version"] == 2 and doc["header"]["status"] == "MONITOR" and doc["actions"][0]["priority_label"] == "Today"
    sys_row = next(r for r in doc["vitals"] if r["key"] == "sys")
    assert sys_row["value"] == 162.0 and sys_row["usual"] == 128.0 and sys_row["diff"] == 34.0 and sys_row["status"] == "HIGH"       # z 2.1 >= band
    md = out["text"]
    assert "## Situation" in md and "## Plan" in md and "| A1 | Today | Caregiver |" in md and "**Escalate if:**" in md
    assert out["doc"]["assessment"] and out["doc"]["changes"]


def test_references_are_numbered_and_a_non_normal_day_without_a_source_says_so(env):
    eng, tmp = env
    out = A.ReportAgent(eng, None, root=tmp).daily(bp_facts())      # the local index of the test environment may or may not hold sources
    ag = make(env, FakeLLM([answer(guidance=[{"text": "Recheck the blood pressure later in the day.", "cites": ["KB-7"]}])]),
              retriever=GatedRetriever(lambda ft, d=None: [KB], pubmed=None))
    o = ag.daily(bp_facts(day="2019-05-02"))
    assert o["mode"] == "llm" and "## References\n1. [KB-7] Hypertension in adults. NICE NG136" in o["text"]
    assert o["doc"]["references"][0]["n"] == 1 and o["doc"]["references"][0]["id"] == "KB-7" and o["doc"]["guidance"][0]["ref_numbers"] == [1]
    none = make(env, FakeLLM([answer()]), retriever=GatedRetriever(lambda ft, d=None: [], pubmed=None)).daily(bp_facts(day="2019-05-03"))
    assert none["doc"]["references"] == [] and "No supporting source was retrieved" in none["text"]
    assert out["doc"]["header"]["mode"] == "template"


def test_sources_are_fetched_by_code_into_the_first_prompt_so_a_report_is_one_request(env, tmp_path):
    pm = client(tmp_path, FakeNCBI())
    ag = make(env, FakeLLM([answer(guidance=[{"text": "Repeat home readings to interpret a single high value.", "cites": ["PM-12345678"]}])]), retriever=GatedRetriever(lambda ft, d=None: [KB], pubmed=pm))
    llm = ag.budget.backends[0].client
    out = ag.daily(bp_facts())
    assert out["mode"] == "llm" and llm.calls == 1 and out["provenance"]["tools"] == []
    p = llm.prompts[0]
    assert "KB-7" in p and "PM-12345678" in p and "proposed_plan" in p and "escalation_limits" in p
    assert pm.requests == 2 and out["provenance"]["sources_cited"] == ["PM-12345678"] and len(out["provenance"]["sources_retrieved"]) == 2
    assert out["doc"]["references"][0]["url"] == "https://pubmed.ncbi.nlm.nih.gov/12345678/" and out["doc"]["references"][0]["publisher"] == "Age and Ageing"


def act(priority="TODAY", owner="CAREGIVER", text="Repeat the reading later today.", **kw):
    return {"priority": priority, "owner": owner, "text": text, "escalate_if": "", "cites": ["F1"], **kw}


def test_priority_now_is_only_for_a_fall_or_a_critical_reading(env):
    out = make(env, FakeLLM([answer(actions=[act("NOW")])])).daily(bp_facts())
    assert out["mode"] == "template" and "priority_invalid" in out["provenance"]["validator_codes"]
    out = make(env, FakeLLM([answer(actions=[act("TODAY")])])).daily(fall_facts())          # a fall plan has a NOW action: the model may not drop it
    assert out["mode"] == "template" and out["doc"]["actions"][0]["priority"] == "NOW"


def test_a_plan_needs_a_caregiver_action(env):
    out = make(env, FakeLLM([answer(actions=[act(owner="CLINICIAN")])])).daily(bp_facts())
    assert out["mode"] == "template" and "priority_invalid" in out["provenance"]["validator_codes"]


def test_a_model_plan_may_reword_but_its_numbers_and_escalation_limits_are_checked(env):
    th = ACT.thresholds()
    ok = act(text="Measure again this evening, seated; write down 162 and the new value.", escalate_if=f"a repeat reading is systolic {th['sys_high']} or higher: seek urgent medical help")
    assert make(env, FakeLLM([answer(actions=[ok])])).daily(bp_facts())["mode"] == "llm"
    bad = act(escalate_if="a repeat reading is systolic 150 or higher: seek urgent medical help")
    assert make(env, FakeLLM([answer(actions=[bad])])).daily(bp_facts(day="2019-05-02"))["mode"] == "template"        # 150 is not an escalation limit


def test_you_have_is_banned_in_findings_but_fine_in_instructions_to_the_caregiver():
    pay = {"facts": bp_facts()}
    assert validate_report({"actions": [act(text="If you have any doubt, call the care team.")]}, pay, [], ["F1"])["ok"]
    assert not validate_report({"changes": [{"text": "You have high blood pressure.", "cites": ["F1"]}]}, pay, [], ["F1"])["ok"]


def test_pubmed_articles_must_be_about_older_people_and_the_findings_subject():
    obesity = {"origin": "pubmed", "title": "Origins of balance disorders during a daily living movement in obese people", "text": "Obese people suffer from postural deficits and are more subject to falls."}
    older = {"origin": "pubmed", "title": "Falls and long lie in older adults", "text": "Older adults who fall and cannot get up (a long lie) are at risk."}
    assert not relevance.keep("fall_inactivity", obesity) and relevance.keep("fall_inactivity", older)
    assert not relevance.keep("agitation", {"origin": "pubmed", "title": "Sleep in children", "text": "Children sleep."})
    assert relevance.keep("fall", {"origin": "local", "title": "Falls", "text": "x"})            # local guideline chunks are not restricted here


def test_doc_markdown_renders_tables_and_signs():
    f = bp_facts()
    d = DOC.build_doc(f, alerts=[], situation="s", background=["b"], items={"assessment": [], "changes": [{"text": "c", "cites": ["F1"]}], "actions": ACT.build(f), "guidance": []},
                      sources=[], case=None, mode="template", data_note=None, trend_days=[])
    md = DOC.render_markdown(d)
    assert "| Systolic blood pressure | 162 mmHg | 128 mmHg | +34 | Above usual (+2.1 SD) |" in md
    assert md.count("## References") == 1
