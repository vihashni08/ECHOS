"""Weekly report evidence mapping: retrieval PER FINDING TYPE present in the week, a finding -> sources mapping, and 'Considerations for clinician review'.

Findings (only when the week has such days): falls, vitals, routine (unusual activity pattern). For each one:
  local guideline chunks first (score >= config weekly.evidence.local_min_score), then at most 1 PubMed article (last 15 years; review / guideline /
  meta-analysis / systematic-review types, unfiltered only when nothing returns); a source is kept ONLY if its title or text matches the finding's terms.
  At most weekly.evidence.max_sources_per_finding (2) per finding.
Considerations: 2-4 short, non-prescriptive bullets, each ending with a citation id of a source attached to that finding. Template mode uses the fixed wording
below; an LLM may reword them (see `reword`), but `validate_considerations` (no LLM) rejects a bullet without a citation, a citation outside the retrieved
set, a citation that is not a source of the bullet's finding, diagnostic wording and medication advice."""
from __future__ import annotations

import logging
import re
from typing import Callable, Optional

from echos.config import load_config
from echos.reports.validator import BANNED

log = logging.getLogger("echos.evidence")

# finding key -> (retrieval finding types tried in order, terms a source must contain at least one of (case-insensitive, whole-word prefix match))
FINDINGS = {
    "falls": {"types": ["fall_inactivity", "fall"], "terms": ["fall", "falls", "post-fall", "fall detection"]},
    "vitals": {"types": ["blood_pressure"], "terms": ["blood pressure", "hypertension", "hypotension", "heart rate", "vital sign", "early warning", "pulse"]},
    "routine": {"types": ["routine_change"], "terms": ["dementia", "agitation", "daily routine", "routine", "sleep", "activity pattern", "restless", "distress"]},
}
# fixed wording per finding key (template mode): non-prescriptive, no diagnosis, no medicines
WORDING = {
    "falls": "Consider reviewing the circumstances of the possible fall(s) this week, and whether a post-fall check or a falls-risk review is appropriate",
    "falls_inactive": "Little movement followed a possible fall; consider clarifying with the caregiver how the person was afterwards",
    "vitals": "Consider whether repeating the readings at rest and comparing them with this person's usual range would be useful",
    "routine": "Consider whether the change in night-time or daily activity warrants a conversation about routine, comfort and possible triggers",
}
# second bullet of a finding, used only when the finding has a second source (it cites that source)
WORDING2 = {
    "falls": "Consider whether a review of mobility, footwear and the home environment is appropriate",
    "vitals": "Consider whether the pattern of readings over the coming days should be looked at again",
    "routine": "Consider asking the caregiver whether anything changed in the person's days, such as sleep, discomfort or new surroundings",
}


def cfg() -> dict:
    return {"local_min_score": 0.5, "max_sources_per_finding": 2, "max_pubmed_per_finding": 1,
            **((load_config().get("weekly") or {}).get("evidence") or {})}


def matches(src: dict, terms: list[str]) -> bool:
    """True when the source's title / text mentions one of the finding's terms (word-start match)."""
    hay = f"{src.get('title', '')} {src.get('text', '')}".lower()
    return any(re.search(r"\b" + re.escape(t.lower()), hay) for t in terms)


def present_findings(d: dict) -> list[dict]:
    """[{key, label, n}] for the finding types that exist in this week's data (label e.g. 'Possible falls: 3')."""
    out = []
    if d["falls"]:
        out.append({"key": "falls", "label": f"Possible falls: {len(d['falls'])}", "n": len(d["falls"]),
                    "inactivity": any(f.get("inactivity") for f in d["falls"])})
    if d["vitals_alerts"]:
        out.append({"key": "vitals", "label": f"Vitals alerts: {len(d['vitals_alerts'])}", "n": len(d["vitals_alerts"])})
    if d["unusual_days"]:
        out.append({"key": "routine", "label": f"Unusual activity pattern days: {len(d['unusual_days'])}", "n": len(d["unusual_days"])})
    return out


def pick_sources(key: str, raw: list[dict], c: dict) -> list[dict]:
    """Select from the retriever's merged result: local chunks above the cut-off that match, then at most max_pubmed matching PubMed articles."""
    terms = FINDINGS[key]["terms"]
    local = [s for s in raw if s.get("origin") == "local" and (s.get("score") is None or s["score"] >= c["local_min_score"]) and matches(s, terms)]
    pm = [s for s in raw if s.get("origin") == "pubmed" and matches(s, terms)][:c["max_pubmed_per_finding"]]
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


def template_considerations(findings: list[dict]) -> list[dict]:
    """2-4 bullets from the fixed wording, each citing the first source of its finding. A finding without a source gets no bullet."""
    out = []
    for f in findings:
        if not f["sources"]:
            continue
        key = "falls_inactive" if f["key"] == "falls" and f.get("inactivity") else f["key"]
        out.append({"finding": f["key"], "text": WORDING[key], "cites": [f["sources"][0]]})
    for f in findings:
        if len(f["sources"]) > 1:
            out.append({"finding": f["key"], "text": WORDING2[f["key"]], "cites": [f["sources"][1]]})
    return out[:4]


def render_bullet(b: dict) -> str:
    return f"{b['text'].rstrip('. ')}. [{', '.join(b['cites'])}]"


def validate_considerations(bullets: list, findings: list[dict], retrieved_ids: set) -> dict:
    """Non-LLM check. Rejects: a bullet without a citation, a citation not in the retrieved set, a citation that is not a source of the bullet's finding,
    diagnostic wording, medication advice; also a count outside 2-4 when sources exist."""
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
        for pat in BANNED:
            if re.search(pat, txt, re.I):
                errs.append(f"bullet {i + 1} contains diagnostic or medication wording")
                break
    if any(f["sources"] for f in findings) and bullets and not 1 <= len(bullets) <= 4:
        errs.append("2-4 bullets expected")
    return {"ok": not errs, "errors": errs}


def drop_mismatched(findings: list[dict]) -> list[dict]:
    """Safety net for the stored mapping: remove from every finding any source whose text does not match that finding."""
    for f in findings:
        keep = [s for s in f.get("source_objs", []) if matches(s, FINDINGS[f["key"]]["terms"])]
        f["source_objs"], f["sources"] = keep, [s["id"] for s in keep]
    return findings


def reword(findings: list[dict], bullets: list[dict], rewriter: Optional[Callable[[list[dict]], list[dict]]], retrieved_ids: set) -> tuple[list[dict], dict]:
    """LLM mode: `rewriter(bullets) -> bullets` (built on the budget manager by the caller). The result is used only if it passes the validator."""
    if rewriter is None:
        return bullets, {"ok": True, "errors": [], "mode": "template"}
    try:
        new = rewriter(bullets)
        v = validate_considerations(new, findings, retrieved_ids)
        if v["ok"] and new:
            return new, {**v, "mode": "llm"}
        return bullets, {**v, "mode": "template", "fallback": "rewrite rejected by the validator"}
    except Exception as ex:   # noqa: BLE001
        return bullets, {"ok": True, "errors": [], "mode": "template", "fallback": f"rewrite failed ({type(ex).__name__})"}
