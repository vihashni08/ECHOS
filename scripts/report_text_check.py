"""AUTOMATED report-text check: a PROXY, not a reader study. Scores the stored TEMPLATE daily reports of a database on
  - reading level (Flesch-Kincaid grade and Flesch reading ease, own syllable heuristic: roughly right for English, not exact),
  - length (words per report, words per sentence),
  - named next steps (every status: >= 2 'What to monitor today' items, each opening with or containing an action verb; URGENT: an Alerts section),
  - banned wording (the validator's diagnostic / medication / agitation-score rules plus model jargon: SHAP, XGBoost, z-score, CUSUM, p-value, ...),
  - no leftover template syntax or empty sections.
It says nothing about whether a real reader understands or finds a report useful (the rating kit for that is unused), and it checks template wording only (no LLM text).
Writes reports/metrics/report_text_check.json (aggregates only: no patient ids, no report text).
Usage: PYTHONPATH=. .venv/bin/python scripts/report_text_check.py [--db data/echos_ui_dev.db]"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from sqlalchemy import text

from echos.orchestrator import state as S
from echos.reports.validator import BANNED

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports/metrics/report_text_check.json"
# The criteria are stated here, fixed before looking at the numbers; they are conventional plain-language targets, not clinical standards.
MAX_GRADE, MAX_WORDS, MIN_MONITOR_ITEMS = 10.0, 220, 2
JARGON = [r"\bshap\b", r"\bxgboost\b", r"\bz-?scores?\b", r"\bcusum\b", r"\bp-?values?\b", r"\blogit\b", r"\bfeature importance\b", r"\bclassifier\b", r"\bthreshold(s)? of\b",
          r"\bstandard deviations?\b", r"\bpr-?auc\b", r"\bbaseline drift\b"]
ACTION = re.compile(r"\b(check|ask|make sure|look|note|watch|keep|encourage|offer|repeat|recheck|re-check|measure|call|contact|review|confirm|follow|compare|record|stay|see|visit|help|remind)\b", re.I)
TEMPLATE_LEFTOVER = re.compile(r"\{\{|\}\}|\{%|%\}|\bNone\b|\bnan\b|\bNaN\b")


def syllables(w: str) -> int:
    w = re.sub(r"[^a-z]", "", w.lower())
    if not w:
        return 0
    n = len(re.findall(r"[aeiouy]+", w))
    if w.endswith("e") and not w.endswith(("le", "ee")) and n > 1:
        n -= 1
    return max(1, n)


def sentences(t: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", t) if re.search(r"[A-Za-z]{2}", s)]


def readability(t: str) -> dict:
    ss, ws = sentences(t), re.findall(r"[A-Za-z][A-Za-z'-]*", t)
    if not ss or not ws:
        return {"grade": None, "ease": None, "words": 0, "sentences": 0}
    syl = sum(syllables(w) for w in ws)
    wps, spw = len(ws) / len(ss), syl / len(ws)
    return {"grade": 0.39 * wps + 11.8 * spw - 15.59, "ease": 206.835 - 1.015 * wps - 84.6 * spw, "words": len(ws), "sentences": len(ss), "max_sentence_words": max(len(re.findall(r"[A-Za-z]+", s)) for s in ss)}


def sections(md: str) -> dict:
    out, cur = {"_head": []}, "_head"
    for ln in md.splitlines():
        m = re.match(r"##\s+(.*)", ln)
        if m:
            cur = m.group(1).strip()
            out[cur] = []
        else:
            out[cur].append(ln)
    return {k: [x.strip("- ").strip() for x in v if x.strip() and not x.strip().startswith(("---", "_Decision"))] for k, v in out.items()}


def body_text(sec: dict) -> str:
    """The prose a reader gets: everything below the title, minus markdown marks, citation ids and the fixed footer."""
    parts = [x for k, v in sec.items() if k != "_head" for x in v] + [x for x in sec["_head"] if x.lower().startswith("**status")]
    t = " ".join(parts)
    t = re.sub(r"\[[A-Z]{1,3}-?\d+(, *[A-Z]{1,3}-?\d+)*\]", "", t)
    return re.sub(r"[*_#]", "", t)


def check_one(md: str) -> dict:
    sec = sections(md)
    text_ = body_text(sec)
    r = readability(text_)
    mon = sec.get("What to monitor today", [])
    status = (re.search(r"\*\*Status: (\w+)", md) or [None, "?"])[1]
    banned = [p for p in BANNED + JARGON if re.search(p, md, re.I)]
    return {"status": status, **r, "monitor_items": len(mon), "monitor_all_action": bool(mon) and all(ACTION.search(x) for x in mon),
            "banned": len(banned), "leftover": bool(TEMPLATE_LEFTOVER.search(md)), "alerts_section": "Alerts" in sec, "empty_section": any(k != "_head" and not v for k, v in sec.items())}


def aggregate(rows: list[dict]) -> dict:
    def q(vals, p):
        v = sorted(x for x in vals if x is not None)
        return round(v[min(len(v) - 1, int(p * len(v)))], 2) if v else None
    g, w, e = [r["grade"] for r in rows], [r["words"] for r in rows], [r["ease"] for r in rows]
    by = {s: sum(1 for r in rows if r["status"] == s) for s in ("NORMAL", "MONITOR", "URGENT")}
    urgent = [r for r in rows if r["status"] == "URGENT"]
    n = len(rows)
    return {
        "label": "AUTOMATED PROXY CHECK of template daily-report wording. Not a reader study: it does not show that a reader understands or finds a report useful.",
        "n_reports": n, "by_status": by,
        "criteria": {"max_grade": MAX_GRADE, "max_words": MAX_WORDS, "min_monitor_items": MIN_MONITOR_ITEMS, "banned": "validator rules + model jargon: none allowed"},
        "reading_grade": {"median": q(g, 0.5), "p90": q(g, 0.9), "max": q(g, 1.0)}, "reading_ease_median": q(e, 0.5),
        "words": {"median": q(w, 0.5), "p90": q(w, 0.9), "max": max(w) if w else None},
        "max_sentence_words_p90": q([r.get("max_sentence_words") for r in rows], 0.9),
        "pass": {"grade_within_limit": sum(1 for r in rows if r["grade"] is not None and r["grade"] <= MAX_GRADE),
                 "length_within_limit": sum(1 for r in rows if r["words"] <= MAX_WORDS),
                 "named_next_steps": sum(1 for r in rows if r["monitor_items"] >= MIN_MONITOR_ITEMS and r["monitor_all_action"]),
                 "urgent_has_alerts_section": sum(1 for r in urgent if r["alerts_section"]), "urgent_total": len(urgent),
                 "no_banned_wording": sum(1 for r in rows if r["banned"] == 0), "no_template_leftover": sum(1 for r in rows if not r["leftover"]),
                 "no_empty_section": sum(1 for r in rows if not r["empty_section"])},
        "all_checks_pass": sum(1 for r in rows if r["grade"] is not None and r["grade"] <= MAX_GRADE and r["words"] <= MAX_WORDS and r["monitor_items"] >= MIN_MONITOR_ITEMS
                               and r["monitor_all_action"] and r["banned"] == 0 and not r["leftover"] and not r["empty_section"] and (r["status"] != "URGENT" or r["alerts_section"])),
        "caveats": ["Syllable counting is a heuristic; grade levels are approximate and short bullet lists score oddly in formulas made for paragraphs.",
                    "Only template wording is checked; LLM-written reports are not covered and real-LLM wording has not been evaluated.",
                    "Reports come from a replay of one cohort; the action-verb test shows a next step is named, not that it is the right one."],
    }


def load_reports(db: str) -> list[str]:
    eng = S.get_engine(db)
    root = Path(db).with_suffix("").as_posix() + "_files"
    with eng.begin() as c:
        paths = [r[0] for r in c.execute(text("SELECT path FROM reports WHERE kind='daily' AND mode='template'"))]
    out = []
    for p in paths:
        f = Path(p) if Path(p).is_absolute() else Path(root) / p
        if f.exists():
            out.append(f.read_text())
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default="data/echos_ui_dev.db")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)
    docs = load_reports(a.db)
    if not docs:
        print(f"no stored template daily reports found for {a.db}")
        return 1
    res = aggregate([check_one(d) for d in docs])
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=1))
    p = res["pass"]
    print(f"{res['n_reports']} template daily reports (proxy check, not a reader study): median grade {res['reading_grade']['median']}, median words {res['words']['median']}; "
          f"all checks pass for {res['all_checks_pass']}; grade<=limit {p['grade_within_limit']}, named steps {p['named_next_steps']}, no banned wording {p['no_banned_wording']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
