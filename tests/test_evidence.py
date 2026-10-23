"""Evidence agent: retrieval, validator, card building (FakeLLM, never the network), caching, and the agent on the bus."""
import json

import pytest
from sqlalchemy import func, select

from echos.evidence import corpus, validator
from echos.evidence.explain import build_card
from echos.evidence.llm import FakeLLM, LLMError
from echos.orchestrator import state as S
from echos.team import bus
from echos.team.evidence_agent import EvidenceAgent

NG136 = ("1.5 Identifying who to refer for same-day specialist review 1.5.1 If a person has severe hypertension clinic blood pressure of 180/120 mmHg or higher "
         "but no symptoms or signs indicating same-day referral carry out investigations for target organ damage and review within 7 days " * 2)
NEWS = ("A single parameter red score of 3 should prompt urgent review by a clinician to decide whether escalation of care is required pulse temperature systolic " * 3)


@pytest.fixture()
def retriever(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "NICE-NG136.txt").write_text(NG136)
    (raw / "RCP-NEWS2.txt").write_text(NEWS)
    corpus.build_index(raw, tmp_path / "idx.joblib")
    return corpus.Retriever(tmp_path / "idx.joblib")


URGENT = {"kind": "urgent", "reasons": ["URGENT: systolic 192 at or above 180 mmHg"], "vitals": {"sys": {"value": 192}}}


def _ok(rid):
    return {"explanation": f"Blood pressure 192 is at or above 180 so the guideline says to look for target organ damage [{rid}].", "citations": [rid]}


def test_retrieval_finds_the_right_document(retriever):
    ps = retriever.search("severe hypertension 180/120 same-day assessment", k=2, docs={"NICE-NG136"})
    assert ps and ps[0].doc == "NICE-NG136" and ps[0].id.startswith("NICE-NG136-p1-")
    assert retriever.search("agitation distress dementia", k=2, docs={"NICE-NG97"}) == []        # a topic with no document has no source


def test_validator_accepts_a_traceable_text_and_rejects_the_rest(retriever):
    p = [x.to_dict() for x in retriever.search("severe hypertension 180/120", k=1)]
    rid, facts = p[0]["id"], "systolic 192 at or above 180 mmHg"
    assert validator.validate(json.dumps(_ok(rid)), facts, p)[0]
    bad = {
        "invented number": {"explanation": f"BP 201 is high [{rid}].", "citations": [rid]},
        "unknown citation": {"explanation": "Check organ damage [X-p1-9].", "citations": ["X-p1-9"]},
        "no citation": {"explanation": "Check for target organ damage.", "citations": []},
        "medication": {"explanation": f"Prescribe a drug [{rid}].", "citations": [rid]},
        "diagnosis": {"explanation": f"This is a diagnosis of hypertension [{rid}].", "citations": [rid]},
    }
    assert validator.validate(json.dumps({"explanation": f"The guideline asks for a clinician to review, not a diagnosis here [{rid}].", "citations": [rid]}), facts, p)[0]
    for name, d in bad.items():
        assert not validator.validate(json.dumps(d), facts, p)[0], name
    assert not validator.validate("not json", facts, p)[0]


def test_card_uses_gemini_text_only_when_it_validates(retriever):
    rid = retriever.search("severe hypertension 180/120", k=1, docs={"NICE-NG136"})[0].id
    good = build_card(URGENT, retriever, FakeLLM([_ok(rid)]))
    assert good["source"] == "gemini" and good["validator"]["ok"] and good["citations"] == [rid]
    for reply in ({"explanation": f"BP 201 [{rid}]", "citations": [rid]}, "garbage", LLMError("timeout")):
        c = build_card(URGENT, retriever, FakeLLM([reply]))
        assert c["source"] == "template" and c["passages"] and c["citations"] and c["validator"]["ok"] is False


def test_no_passage_means_no_llm_call_and_an_honest_card(retriever):
    llm = FakeLLM([{"explanation": "x", "citations": []}])
    c = build_card({"kind": "review", "reasons": ["agitation risk 24% in the afternoon window"]}, retriever, llm)
    assert llm.calls == 0 and c["passages"] == [] and "No supporting guideline passage" in c["explanation"]


def test_answers_are_cached(retriever):
    rid = retriever.search("severe hypertension 180/120", k=1, docs={"NICE-NG136"})[0].id
    store, llm = {}, FakeLLM([_ok(rid)])
    cache = (store.get, lambda k, t, m: store.__setitem__(k, t))
    build_card(URGENT, retriever, llm, cache=cache)
    build_card(URGENT, retriever, llm, cache=cache)
    assert llm.calls == 1


def test_agent_is_idempotent_and_consumes_requests(tmp_path, retriever):
    eng = S.get_engine(tmp_path / "e.db")
    for _ in range(2):                                           # the same request twice (a restart re-sends)
        bus.post(eng, "care", "evidence", "evidence_request", "p1", "2019-05-03", {"kind": "urgent", "ref": "a1", "reasons": URGENT["reasons"], "vitals": URGENT["vitals"]})
    ag = EvidenceAgent(eng, retriever=retriever, llm=None)
    assert ag.run_until_idle() == 2 and ag.step() == 0
    with eng.begin() as c:
        assert c.execute(select(func.count()).select_from(S.evidence_card)).scalar() == 1
        card = json.loads(c.execute(select(S.evidence_card.c.card)).scalar())
    assert card["source"] == "template" and card["passages"][0]["doc"] == "NICE-NG136"


def test_urgent_cards_come_first(tmp_path):
    eng = S.get_engine(tmp_path / "u.db")
    for k in range(3):
        bus.post(eng, "care", "evidence", "evidence_request", f"r{k}", "2019-05-03", {"kind": "review", "ref": str(k), "reasons": ["agitation risk 9%"]})
    bus.post(eng, "care", "evidence", "evidence_request", "p1", "2019-05-03", {"kind": "urgent", "ref": "a1", "reasons": URGENT["reasons"]})
    ag = EvidenceAgent(eng, retriever=None, llm=None)
    ag.step()
    with eng.begin() as c:
        assert c.execute(select(S.evidence_card.c.kind)).scalars().all() == ["urgent"]
