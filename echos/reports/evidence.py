"""Weekly report evidence mapping: retrieval PER FINDING TYPE present in the week, a finding -> sources mapping, and 'Considerations for clinician review'.

Findings (only when the week has such days): falls, vitals, routine (unusual activity pattern). For each one:
  local guideline chunks first (score >= config weekly.evidence.local_min_score), then at most 1 PubMed article (last 15 years; review / guideline /
  meta-analysis / systematic-review types, unfiltered only when nothing returns); a source is kept ONLY if its title or text matches the finding's terms.
  At most weekly.evidence.max_sources_per_finding (2) per finding.
Vitals findings are direction-aware (bp_high, bp_low, hr_high, hr_low, temperature, weight_change, and vitals_news2 for a combination of readings): each has
its own local query, PubMed terms and relevance terms (echos/knowledge/relevance.py); a source that does not match its finding (e.g. hypertension guidance for
low blood pressure) is dropped, and a finding with no relevant source shows 'no supporting source retrieved'.
Considerations: 0-4 short, non-prescriptive bullets, each naming a concrete value, count, date or event of the week and ending with a citation id of a source
attached to that finding. Template mode builds them from the week's data; an LLM may reword them (see `reword`), but `validate_considerations` (no LLM) rejects a
bullet without a citation, a citation outside the retrieved set, a citation that is not a source of the bullet's finding, diagnostic wording, medication advice
and a generic bullet that contains no number, date or event reference. Fewer than 2 qualifying bullets are printed as fewer bullets, never as filler."""
from __future__ import annotations

import calendar
import logging
import re
from typing import Callable, Optional

from echos.config import load_config
from echos.knowledge import relevance
from echos.reports import vitals_why as VW
from echos.reports.validator import BANNED, CITE_TOKEN, NUM

log = logging.getLogger("echos.evidence")

# finding key -> (retrieval finding types tried in order, terms a source must contain at least one of (case-insensitive, whole-word prefix match))
FINDINGS = {
    "falls": {"types": ["fall_inactivity", "fall"], "terms": ["fall", "falls", "post-fall", "fall detection"]},
    "vitals_news2": {"types": ["vitals_news2"], "terms": ["blood pressure", "heart rate", "vital sign", "early warning", "pulse"]},
    "routine": {"types": ["routine_change"], "terms": ["dementia", "agitation", "daily routine", "routine", "sleep", "activity pattern", "restless", "distress"]},
    **{k: {"types": [k], "terms": r["terms"]} for k, r in relevance.RULES.items()},
}
VITALS_KEYS = ["bp_high", "bp_low", "hr_high", "hr_low", "temperature", "weight_change", "vitals_news2"]
LABELS = {"bp_high": "Blood pressure above usual", "bp_low": "Blood pressure below usual", "hr_high": "Heart rate above usual", "hr_low": "Heart rate below usual",
          "temperature": "Temperature outside usual", "weight_change": "Weight change", "vitals_news2": "Readings differing from usual in combination"}
# what a reading/event of each finding is called in a bullet, and the non-prescriptive closing clause
NOUN = {"bp_high": "high blood pressure", "bp_low": "low blood pressure", "hr_high": "high heart rate", "hr_low": "low heart rate", "temperature": "temperature",
        "weight_change": "weight change", "vitals_news2": "combined-reading"}
CLOSE = {
    "falls": "consider whether a post-fall check or a falls-risk review is appropriate",
    "vitals": "consider whether repeating the reading at rest and comparing it with this person's usual range would be useful",
    "routine": "consider whether the change in night-time or daily activity warrants a conversation about routine, comfort and possible triggers",
}
CLOSE2 = {
    "falls": "consider whether a review of mobility, footwear and the home environment is appropriate",
    "vitals": "consider whether a repeat reading under the same conditions would add information",
    "routine": "consider asking the caregiver whether anything changed in the person's days, such as sleep, discomfort or new surroundings",
}
MONTHS = [m.lower() for m in calendar.month_name[1:]] + [m.lower() for m in calendar.month_abbr[1:]]
WEEKDAYS = [d.lower() for d in calendar.day_name]
NUMWORDS = ["one", "two", "three", "four", "five", "six", "seven", "once", "twice"]


def day_text(day: str) -> str:
    return f"{int(day[8:10])} {calendar.month_name[int(day[5:7])]}"


def cfg() -> dict:
    return {"local_min_score": 0.5, "max_sources_per_finding": 2, "max_pubmed_per_finding": 1,
            **((load_config().get("weekly") or {}).get("evidence") or {})}


def matches(src: dict, terms: list[str]) -> bool:
    """True when the source's title / text mentions one of the finding's terms (word-start match)."""
    hay = f"{src.get('title', '')} {src.get('text', '')}".lower()
    return any(re.search(r"\b" + re.escape(t.lower()), hay) for t in terms)


def source_ok(key: str, src: dict) -> bool:
    """A source belongs to a finding only if it matches the finding's terms and is not excluded for it (e.g. hypertension guidance for low blood pressure)."""
    return matches(src, FINDINGS[key]["terms"]) and relevance.keep(key, src)


def entry_types(a: dict) -> list[str]:
    """Finding types of one vitals alert row (rows saved before the direction split are classified from their values)."""
    if a.get("types"):
        return a["types"]
    return VW.classify(a.get("values") or {}, a.get("baselines") or {}, critical=a.get("kind") == "critical")["types"]


def present_findings(d: dict) -> list[dict]:
    """[{key, label, n}] for the finding types that exist in this week's data (label e.g. 'Possible falls: 3')."""
    out = []
    if d["falls"]:
        out.append({"key": "falls", "label": f"Possible falls: {len(d['falls'])}", "n": len(d["falls"]),
                    "inactivity": any(f.get("inactivity") for f in d["falls"])})
    for k in VITALS_KEYS:
        n = sum(k in entry_types(a) for a in d["vitals_alerts"])
        if n:
            out.append({"key": k, "label": f"{LABELS[k]}: {n}", "n": n})
    if d["unusual_days"]:
        out.append({"key": "routine", "label": f"Unusual activity pattern days: {len(d['unusual_days'])}", "n": len(d["unusual_days"])})
    return out


def pick_sources(key: str, raw: list[dict], c: dict) -> list[dict]:
    """Select from the retriever's merged result: local chunks above the cut-off that match, then at most max_pubmed matching PubMed articles."""
    local = [s for s in raw if s.get("origin") == "local" and (s.get("score") is None or s["score"] >= c["local_min_score"]) and source_ok(key, s)]
    pm = [s for s in raw if s.get("origin") == "pubmed" and source_ok(key, s)][:c["max_pubmed_per_finding"]]
    room = c["max_sources_per_finding"] - len(pm)
    return local[:max(room, 0)] + pm


def gather_findings(d: dict, retriever, status: str) -> tuple[list[dict], list[dict], list[str]]:
    """-> (findings, flat unique sources, notes). findings: [{key, label, n, sources: [ids], source_objs}] ; retrieval is skipped for a week with no
    Monitor/Urgent day. One retriever call per finding type present (never one call per week)."""
    notes: list[str] = []
    fl = present_findings(d)
    if not d["any_monitor_or_urgent"] or retriever is None:
        notes.append("retrieval skipped: no Monitor or Urgent day" if not d["any_monitor_or_urgent"] else "no retriever")
        return [{**f, "sources": []} for f in fl], [], notes
    c, flat, seen = cfg(), [], set()
    out = []
    for f in fl:
        spec = FINDINGS[f["key"]]
        ft = spec["types"][0] if f["key"] != "falls" or f.get("inactivity") else spec["types"][1]
        try:
            r = retriever.retrieve(status, ft, {"post_fall_inactive": bool(f.get("inactivity"))} if f["key"] == "falls" else {})
            raw = list(r["sources"])
            notes += [f"{f['key']}: {e}" for e in r["events"]]
        except Exception as ex:   # noqa: BLE001 - references are optional
            notes.append(f"{f['key']}: retrieval failed ({type(ex).__name__})")
            raw = []
        picked = pick_sources(f["key"], raw, c)
        dropped = len(raw) - len(picked)
        if dropped:
            notes.append(f"{f['key']}: {dropped} retrieved source(s) dropped (below the relevance cut-off {c['local_min_score']}, not matching the finding, or over the per-finding limit)")
        out.append({**f, "sources": [s["id"] for s in picked], "source_objs": picked})
        for s in picked:
            if s["id"] not in seen:
                seen.add(s["id"])
                flat.append(s)
    return out, flat, notes


def _vals_text(vals: dict) -> str:
    bp = f"systolic {VW._g(vals['sys'])} / diastolic {VW._g(vals['dia'])} mmHg" if vals.get("sys") is not None and vals.get("dia") is not None else None
    rest = [f"{VW.NAMES[k]} {VW._g(vals[k])} {VW.UNITS[k]}" for k in ("sys", "dia", "hr", "temp", "weight") if vals.get(k) is not None and not (bp and k in ("sys", "dia"))]
    return ", ".join(([bp] if bp else []) + rest)


def _entry_fact(a: dict, key: str) -> str:
    """Concrete clause for one vitals alert row and finding key, e.g. 'Diastolic 58 mmHg on 15 May, 9 below usual'."""
    day, vals, top = day_text(a["day"]), a.get("values") or {}, a.get("top")
    if top and VW._type(top["var"], top["delta"]) == key:
        v = top["var"]
        return (f"{VW.NAMES[v].capitalize()} {VW._g(top['value'])} {VW.UNITS[v]} on {day}, {VW._g(abs(top['delta']))} "
                f"{'above' if top['delta'] > 0 else 'below'} usual")
    br = [b for b in VW.critical_breaches(vals) if b["type"] == key] if a.get("kind") == "critical" else []
    if br:
        v = br[0]["var"]
        return f"{VW.NAMES[v].capitalize()} {VW._g(vals[v])} {VW.UNITS[v]} on {day}, {br[0]['side']} the fixed safety limit of {VW._g(br[0]['limit'])}"
    lead = a.get("lead")
    if lead:
        v = lead["var"]
        return (f"{VW.NAMES[v].capitalize()} {VW._g(lead['value'])} {VW.UNITS[v]} on {day}, {VW._g(abs(lead['delta']))} {'above' if lead['delta'] > 0 else 'below'} usual, "
                f"inside the usual range but part of a combination of readings that differs from usual")
    return f"Readings on {day} ({_vals_text(vals)}) differed from usual in combination" if vals else f"Readings on {day} differed from usual in combination"


def _size(a: dict) -> float:
    t = a.get("top") or a.get("lead")
    return abs(t["delta"]) / (abs(t["base"]) or 1) if t else 0.0


def finding_facts(f: dict, d: dict) -> tuple[Optional[str], Optional[str]]:
    """(first clause, second clause) naming concrete values / counts / dates of this finding in the week; (None, None) if there is nothing to name."""
    k = f["key"]
    if k == "falls":
        fl = sorted(d["falls"], key=lambda x: (x["day"], x["time"] or ""))
        if not fl:
            return None, None
        last, n = fl[-1], len(fl)
        c1 = f"{n} possible fall{'' if n == 1 else 's'} this week, the latest on {day_text(last['day'])}" + (f" at {last['time']}" if last.get("time") else "") \
             + (", followed by little movement" if last.get("inactivity") else "")
        return c1, f"{n} possible fall{'' if n == 1 else 's'} this week (latest {day_text(last['day'])})"
    if k == "routine":
        u = sorted(x["day"] for x in d["unusual_days"])
        if not u:
            return None, None
        n = len(u)
        return (f"Activity pattern differed from usual on {n} day{'' if n == 1 else 's'} ({', '.join(day_text(x) for x in u[:4])})",
                f"Activity pattern differed from usual on {n} day{'' if n == 1 else 's'}, the latest {day_text(u[-1])}")
    rows = sorted((a for a in d["vitals_alerts"] if k in entry_types(a)), key=lambda a: a["day"])
    if not rows:
        return None, None
    best = max(rows, key=_size)
    days = sorted({a["day"] for a in rows})
    return (_entry_fact(best, k),
            f"{len(rows)} {NOUN[k]} alert{'' if len(rows) == 1 else 's'} this week ({', '.join(day_text(x) for x in days[:4])})")


def template_considerations(findings: list[dict], d: Optional[dict] = None) -> list[dict]:
    """0-4 bullets built from the week's data, each naming a concrete value / count / date, closing with a non-prescriptive consideration and citing the
    first source of its finding. A finding without a source, or with nothing concrete to name, gets no bullet; no filler is added."""
    out, second = [], []
    for f in findings:
        if not f["sources"] or d is None:
            continue
        grp = "vitals" if f["key"] in VITALS_KEYS else ("falls" if f["key"] == "falls" else "routine")
        c1, c2 = finding_facts(f, d)
        if c1:
            close = "consider clarifying with the caregiver how the person was afterwards" if grp == "falls" and f.get("inactivity") else CLOSE[grp]
            out.append({"finding": f["key"], "text": f"{c1}; {close}", "cites": [f["sources"][0]]})
        if c2 and len(f["sources"]) > 1:
            second.append({"finding": f["key"], "text": f"{c2}; {CLOSE2[grp]}", "cites": [f["sources"][1]]})
    return [b for b in (out + second) if is_concrete(b["text"])][:4]


def render_bullet(b: dict) -> str:
    return f"{b['text'].rstrip('. ')}. [{', '.join(b['cites'])}]"


def is_concrete(text: str) -> bool:
    """True when the bullet names a number, a date (month / weekday) or a counted event. Citation ids are not numbers."""
    t = CITE_TOKEN.sub(" ", str(text)).lower()
    return bool(NUM.search(t) or any(re.search(r"\b" + m + r"\b", t) for m in MONTHS + WEEKDAYS + NUMWORDS))


def validate_considerations(bullets: list, findings: list[dict], retrieved_ids: set) -> dict:
    """Non-LLM check. Rejects: a bullet without a citation, a citation not in the retrieved set, a citation that is not a source of the bullet's finding,
    diagnostic wording, medication advice, a generic bullet with no number / date / event reference; also more than 4 bullets."""
    errs = []
    by_key = {f["key"]: set(f["sources"]) for f in findings}
    for i, b in enumerate(bullets):
        txt, cites = str(b.get("text", "")), list(b.get("cites") or [])
        if not cites:
            errs.append(f"bullet {i + 1} has no citation")
        for cid in cites:
            if cid not in retrieved_ids:
                errs.append(f"bullet {i + 1} cites {cid}, which was not retrieved")
            elif cid not in by_key.get(b.get("finding"), set()):
                errs.append(f"bullet {i + 1} cites {cid}, which is not a source of the finding '{b.get('finding')}'")
        if not is_concrete(txt):
            errs.append(f"bullet {i + 1} is generic: it names no value, count, date or event of the week")
        for pat in BANNED:
            if re.search(pat, txt, re.I):
                errs.append(f"bullet {i + 1} contains diagnostic or medication wording")
                break
    if len(bullets) > 4:
        errs.append("at most 4 bullets expected")
    return {"ok": not errs, "errors": errs}


def drop_mismatched(findings: list[dict]) -> list[dict]:
    """Safety net for the stored mapping: remove from every finding any source whose text does not match that finding."""
    for f in findings:
        keep = [s for s in f.get("source_objs", []) if source_ok(f["key"], s)]
        f["source_objs"], f["sources"] = keep, [s["id"] for s in keep]
    return findings


def reword(findings: list[dict], bullets: list[dict], rewriter: Optional[Callable[[list[dict]], list[dict]]], retrieved_ids: set) -> tuple[list[dict], dict]:
    """LLM mode: `rewriter(bullets) -> bullets` (built on the budget manager by the caller). The result is used only if it passes the validator."""
    if rewriter is None:
        return bullets, {"ok": True, "errors": [], "mode": "template"}
    try:
        new = rewriter(bullets)
        v = validate_considerations(new, findings, retrieved_ids)
        allowed = {float(x) for b in bullets for x in NUM.findall(CITE_TOKEN.sub(" ", b["text"]))}
        extra = sorted({x for b in new for x in NUM.findall(CITE_TOKEN.sub(" ", str(b.get("text", "")))) if float(x) not in allowed})
        if extra:
            v = {"ok": False, "errors": v["errors"] + [f"rewrite introduced number(s) not in the original: {', '.join(extra)}"]}
        if v["ok"] and new:
            return new, {**v, "mode": "llm"}
        return bullets, {**v, "mode": "template", "fallback": "rewrite rejected by the validator"}
    except Exception as ex:   # noqa: BLE001
        return bullets, {"ok": True, "errors": [], "mode": "template", "fallback": f"rewrite failed ({type(ex).__name__})"}
