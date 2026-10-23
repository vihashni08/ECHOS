"""Evaluation of the Evidence agent on a hand-built gold set (scripts/evaluation/evidence_gold.json) -> reports/metrics/v2_evidence.json

Part 1, retrieval (no API): for each case, is a retrieved passage relevant (from a gold document AND containing a key phrase)? hit@1, hit@3, MRR for the cases
that have a source; correct abstention ("no supporting source") for the cases that should have none. Compared with a naive retriever (raw reason text, all
documents, no routing).
Part 2, generation (Gemini; answers cached in data/knowledge/llm_cache.sqlite, so a re-run costs nothing): three ways of writing the explanation,
checked by the non-LLM validator: G0 Gemini without retrieval, G1 Gemini with retrieved passages (raw output), G2 the shipped system (G1 accepted only if the
validator passes, otherwise the fixed template). Part 3: faithfulness labels given by hand to the G2 text (scripts/evaluation/evidence_labels.json).

    PYTHONPATH=. python scripts/evaluation/evidence_eval.py [--retrieval-only]
"""
from __future__ import annotations

import collections
import json
import sqlite3
import sys
import time

from echos.config import ROOT
from echos.evidence import corpus, explain, validator
from echos.evidence.llm import LLMError, make_llm

GOLD = ROOT / "scripts" / "evaluation" / "evidence_gold.json"
LABELS = ROOT / "scripts" / "evaluation" / "evidence_labels.json"
OUT = ROOT / "reports" / "metrics" / "v2_evidence.json"
DUMP = ROOT / "data" / "knowledge" / "eval_outputs.json"
CACHE = ROOT / "data" / "knowledge" / "llm_cache.sqlite"

SYSTEM_NO_RAG = ("You write one short note for a clinician explaining why a person in home dementia monitoring was flagged today. Use the FACTS and your general "
                 "medical knowledge. At most 3 sentences, plain English. Reply with JSON only: {\"explanation\": \"...\", \"citations\": []}.")


def relevant(p, case) -> bool:
    return p.doc in case["gold_docs"] and any(k.lower() in p.text.lower() for k in case["key_phrases"])


def retrieval(r, cases) -> dict:
    out = {"with_source": collections.defaultdict(list), "abstain": []}
    naive = collections.defaultdict(list)
    for c in cases:
        ps = explain.retrieve(r, c["reasons"], 3, 0.08)
        ps_obj = [corpus.Passage(**{k: v for k, v in p.items()}) for p in ps]
        if c["expect_source"]:
            ranks = [i for i, p in enumerate(ps_obj, 1) if relevant(p, c)]
            out["with_source"][c["category"]].append({"id": c["id"], "first_rank": ranks[0] if ranks else None})
            nv = [i for i, p in enumerate(r.search(" ".join(c["reasons"]), k=3, min_score=0.08), 1) if relevant(p, c)]
            naive[c["category"]].append({"id": c["id"], "first_rank": nv[0] if nv else None})
        else:
            out["abstain"].append({"id": c["id"], "category": c["category"], "returned": len(ps_obj)})

    def agg(d):
        rows = [x for v in d.values() for x in v]
        n = len(rows)
        return {"n": n, "hit_at_1": sum(x["first_rank"] == 1 for x in rows) / n, "hit_at_3": sum(x["first_rank"] is not None for x in rows) / n,
                "mrr": sum(1 / x["first_rank"] for x in rows if x["first_rank"]) / n}
    res = {"routed": agg(out["with_source"]), "naive": agg(naive),
           "by_category": {k: {"n": len(v), "hit_at_3": sum(x["first_rank"] is not None for x in v) / len(v)} for k, v in out["with_source"].items()},
           "abstain_cases": len(out["abstain"]), "correct_abstentions": sum(x["returned"] == 0 for x in out["abstain"]),
           "false_sources": [x["id"] for x in out["abstain"] if x["returned"]]}
    return res


class Gen:
    def __init__(self, llm):
        self.llm, self.calls = llm, 0
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(CACHE, timeout=30)
        self.con.execute("CREATE TABLE IF NOT EXISTS llm_cache (key TEXT PRIMARY KEY, response TEXT, model TEXT, ts REAL)")

    def __call__(self, system: str, user: str) -> str | None:
        import hashlib
        key = hashlib.sha256(f"{self.llm.model}|{system}|{user}".encode()).hexdigest()
        r = self.con.execute("SELECT response FROM llm_cache WHERE key=?", (key,)).fetchone()
        if r:
            return r[0]
        try:
            raw = self.llm.generate(system, user)
        except LLMError:
            return None
        self.calls += 1
        with self.con:
            self.con.execute("INSERT OR REPLACE INTO llm_cache VALUES (?,?,?,?)", (key, raw, self.llm.model, time.time()))
        return raw


def kind_of(problem: str) -> str:
    return problem.split(":")[0]


def generation(r, cases, llm) -> tuple[dict, list]:
    gen = Gen(llm)
    rows, dump = [], []
    for c in cases:
        facts = {"kind": c["kind"], "reasons": c["reasons"], "vitals": None}
        ps = explain.retrieve(r, c["reasons"], 3, 0.08)
        if not ps:
            continue
        ft = explain.facts_text(facts)
        user0 = json.dumps({"FACTS": {"kind": c["kind"], "reasons": c["reasons"], "rule": explain.rule_notes(c["reasons"])}}, ensure_ascii=False)
        raw0, raw1 = gen(SYSTEM_NO_RAG, user0), gen(explain.SYSTEM, explain._user_prompt(facts, ps))
        v0 = validator.validate(raw0, ft, []) if raw0 else (False, ["llm: no answer"], None)
        v1 = validator.validate(raw1, ft, ps) if raw1 else (False, ["llm: no answer"], None)
        rows.append({"id": c["id"], "g0_ok": v0[0], "g0_problems": [kind_of(x) for x in v0[1]], "g1_ok": v1[0], "g1_problems": [kind_of(x) for x in v1[1]]})
        text = v1[2]["explanation"] if v1[0] else explain.template_text(facts, ps)
        dump.append({"id": c["id"], "category": c["category"], "reasons": c["reasons"], "source": "gemini" if v1[0] else "template", "text": text,
                     "cited": v1[2]["citations"] if v1[0] else [ps[0]["id"]], "passages": [{"id": p["id"], "text": p["text"][:700]} for p in ps]})
    n = len(rows)
    cnt = lambda key, val: sum(1 for x in rows for p in x[key] if p == val)  # noqa: E731
    res = {"cases": n, "llm_calls_made": gen.calls, "model": llm.model,
           "g0_no_retrieval": {"accepted_by_validator": sum(x["g0_ok"] for x in rows) / n, "with_untraceable_numbers": sum("numbers" in x["g0_problems"] for x in rows) / n,
                               "with_banned_wording": sum("wording" in x["g0_problems"] for x in rows) / n},
           "g1_with_retrieval_raw": {"accepted_by_validator": sum(x["g1_ok"] for x in rows) / n, "rejected": n - sum(x["g1_ok"] for x in rows),
                                     "reject_reasons": {k: sum(k in x["g1_problems"] for x in rows) for k in ("json", "citations", "numbers", "wording", "length", "llm")}},
           "g2_shipped": {"gemini_text_shown": sum(x["g1_ok"] for x in rows) / n, "template_fallback": 1 - sum(x["g1_ok"] for x in rows) / n}}
    return res, dump


def faithfulness(dump) -> dict | None:
    if not LABELS.exists():
        return None
    lab = json.loads(LABELS.read_text())["labels"]
    got = [(d, lab[d["id"]]) for d in dump if d["id"] in lab and d["source"] == "gemini"]
    if not got:
        return None
    c = collections.Counter(v["verdict"] for _, v in got)
    n = len(got)
    return {"labelled_gemini_texts": n, "supported": c["supported"] / n, "partly_supported": c["partly"] / n, "unsupported": c["unsupported"] / n,
            "annotator": json.loads(LABELS.read_text()).get("annotator")}


def main() -> dict:
    gold = json.loads(GOLD.read_text())
    r = corpus.Retriever()
    out = {"gold_cases": len(gold["cases"]), "documents_indexed": sorted({c["doc"] for c in r.chunks}), "retrieval": retrieval(r, gold["cases"]),
           "annotator": gold["annotator"]}
    fr = ROOT / "reports" / "metrics" / "v2_evidence_first_run.json"
    if fr.exists():
        first = json.loads(fr.read_text())["retrieval"]
        out["first_run_retrieval"] = {"routed": first["routed"], "naive": first["naive"], "correct_abstentions": first["correct_abstentions"], "abstain_cases": first["abstain_cases"],
                                      "note": "before the routing fix (event-rate and data-check reasons no longer retrieve) and before two gold categories were widened; see gold 'revisions'"}
    out["gold_revisions"] = gold.get("revisions")
    fg = ROOT / "reports" / "metrics" / "v2_evidence_first_gen.json"
    if fg.exists():
        out["first_run_generation"] = json.loads(fg.read_text())
    llm = None if "--retrieval-only" in sys.argv else make_llm()
    if llm is not None:
        out["generation"], dump = generation(r, gold["cases"], llm)
        DUMP.write_text(json.dumps(dump, indent=1))
        f = faithfulness(dump)
        if f:
            out["faithfulness"] = f
    else:
        out["generation"] = "skipped: no key or --retrieval-only"
    OUT.write_text(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    print(json.dumps(main(), indent=1)[:3500])
