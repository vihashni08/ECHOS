"""Evidence card: why a case was flagged, grounded in retrieved guideline passages.

    facts (structured, from the Patient Monitor)  ->  query  ->  retrieve (TF-IDF)  ->  Gemini writes a short cited explanation
        ->  validator (echos/evidence/validator.py)  ->  accepted text, or the fixed template when anything fails

The model sees ONLY the structured reasons and the retrieved passages (no patient id). It is told to cite passage ids, add no numbers of its own and give
no diagnosis or medication advice. If retrieval finds nothing above `min_score`, the card says so ("no supporting source found") and the LLM is not called."""
from __future__ import annotations

import hashlib
import json
import re
import time

from echos.evidence import validator
from echos.evidence.llm import LLMError

SYSTEM = (
    "You write one short note for a clinician explaining why a person in home dementia monitoring was flagged today. "
    "Use ONLY the FACTS and the PASSAGES provided. Rules: (1) at most 3 sentences, plain English; (2) every statement taken from a passage must carry its id "
    "in square brackets, e.g. [NICE-NG136-p12-3]; put brackets ONLY around passage ids, never around FACTS, reasons or anything else; (3) do not write any number that is not in the FACTS or in a cited passage; (4) no diagnosis, no medication "
    "or dose advice, no treatment instructions: say what the guideline says to check or consider; (5) if the passages do not support a statement, leave it out. "
    'Reply with JSON only: {"explanation": "...", "citations": ["<passage id>", ...]}.')

VITAL_DOCS = {"NICE-NG136", "RCP-NEWS2"}
# (reason pattern, query, documents allowed for this topic). Agitation text comes only from the dementia and delirium guidelines: if they are not indexed there is no source.
TOPICS = [
    (re.compile(r"systolic \d+ at or below|pulse .* outside|temperature .* outside", re.I), "early warning score clinical response red score urgent assessment pulse temperature systolic", {"RCP-NEWS2"}),
    (re.compile(r"at or above (180|120)", re.I), "severe hypertension blood pressure 180/120 same-day specialist assessment target organ damage", {"NICE-NG136"}),
    (re.compile(r"vs usual|SD\)", re.I), "blood pressure monitoring measurement variability home", {"NICE-NG136"}),
    (re.compile(r"agitation", re.I), "agitation distress dementia assess possible causes pain environment non-pharmacological delirium", {"NICE-NG97", "NICE-CG103"}),
    (re.compile(r"TIHM check limits", re.I), "blood pressure heart rate temperature thresholds assessment", VITAL_DOCS),
]


def build_queries(reasons: list[str]) -> list[tuple[str, set]]:
    # Reasons that no topic covers (a personal event rate, a data-quality note) have no guideline behind them: no query, so no source is cited.
    return [(topic, docs) for rx, topic, docs in TOPICS if any(rx.search(r) for r in reasons)]


def build_query(reasons: list[str]) -> str:
    return " ".join(q for q, _ in build_queries(reasons))


def retrieve(retriever, reasons: list[str], k: int, min_score: float) -> list[dict]:
    """Best passages per matched topic (round-robin, no duplicates), at most k."""
    if retriever is None:
        return []
    per = [retriever.search(q, k=k, min_score=min_score, docs=docs) for q, docs in build_queries(reasons)]
    out, seen = [], set()
    for rank in range(k):
        for lst in per:
            if rank < len(lst) and lst[rank].id not in seen and len(out) < k:
                seen.add(lst[rank].id)
                out.append(lst[rank].to_dict())
    return out


RULE_NOTES = [
    (re.compile(r"URGENT: (systolic \d+ at or below|pulse|temperature)", re.I),
     "These limits are the NEWS2 single-parameter red-score limits (Royal College of Physicians 2017). A red score is what triggered the urgent alert."),
    (re.compile(r"URGENT: .* at or above (180|120)", re.I),
     "This is the NICE NG136 severe hypertension threshold (clinic blood pressure 180/120 mmHg or higher). It is what triggered the urgent alert."),
    (re.compile(r"outside TIHM check limits", re.I),
     "These are the TIHM study's own daily-check limits; they are an attention note, not an urgent alert."),
]


def rule_notes(reasons: list[str]) -> list[str]:
    """Which rule produced a reason, in words, so the model need not infer it from the passages (a bridging claim it cannot cite)."""
    return list(dict.fromkeys(note for rx, note in RULE_NOTES if any(rx.search(r) for r in reasons)))


def facts_text(facts: dict) -> str:
    return " ".join(facts.get("reasons") or []) + " " + " ".join(rule_notes(facts.get("reasons") or [])) + " " + json.dumps(facts.get("vitals") or {}, default=str)


def _short(text: str, n: int) -> str:
    w = text.split()
    return " ".join(w[:n]) + (" ..." if len(w) > n else "")


def template_text(facts: dict, passages: list[dict]) -> str:
    why = "; ".join((facts.get("reasons") or [])[:3]) or "selected for review"
    if not passages:
        return f"{why}. No supporting guideline passage was found for this case."
    p = passages[0]
    return f"{why}. Guideline ({p['source']}, p.{p['page']}) [{p['id']}]: \"{_short(p['text'], 30)}\""


def _user_prompt(facts: dict, passages: list[dict]) -> str:
    return json.dumps({"FACTS": {"kind": facts.get("kind"), "reasons": facts.get("reasons"), "rule": rule_notes(facts.get("reasons") or []), "vitals": facts.get("vitals")},
                       "PASSAGES": [{"id": p["id"], "source": p["source"], "page": p["page"], "text": _short(p["text"], 110)} for p in passages]},
                      default=str, ensure_ascii=False)


def build_card(facts: dict, retriever, llm, k: int = 3, min_score: float = 0.08, cache=None) -> dict:
    """cache = (get(key) -> str | None, put(key, text, model)) or None. Never raises: any failure yields the template card."""
    t0 = time.time()
    reasons = facts.get("reasons") or []
    passages = retrieve(retriever, reasons, k, min_score)
    card = {"kind": facts.get("kind"), "reasons": reasons, "passages": passages, "explanation": "", "citations": [], "source": "template",
            "validator": {"ok": None, "problems": []}, "model": None}
    if llm is not None and passages:
        user = _user_prompt(facts, passages)
        key = hashlib.sha256(f"{llm.model}|{SYSTEM}|{user}".encode()).hexdigest()
        try:
            raw = cache[0](key) if cache else None
            if raw is None:
                raw = llm.generate(SYSTEM, user)
                if cache:
                    cache[1](key, raw, llm.model)
            ok, problems, parsed = validator.validate(raw, facts_text(facts), passages)
            card["validator"] = {"ok": ok, "problems": problems}
            card["model"] = llm.model
            if ok:
                card.update(explanation=parsed["explanation"].strip(), citations=[str(c) for c in parsed["citations"]], source="gemini")
        except LLMError as ex:
            card["validator"] = {"ok": False, "problems": [f"llm: {ex}"]}
        except Exception as ex:  # noqa: BLE001 - the card must always exist
            card["validator"] = {"ok": False, "problems": [f"error: {type(ex).__name__}"]}
    if card["source"] == "template":
        card["explanation"] = template_text(facts, passages)
        card["citations"] = [passages[0]["id"]] if passages else []
    card["latency_s"] = round(time.time() - t0, 3)
    return card
