"""Per-finding relevance rules for the direction-aware vitals findings: the local-index query, the PubMed terms and the relevance terms of each type live
next to each other here. A retrieved source is kept for a finding ONLY if its title / text mentions one of the finding's terms and neither its title nor its
document is on the finding's exclusion list (so a low-BP finding never cites hypertension guidance)."""
from __future__ import annotations

import re

# finding type -> {query (local index), pubmed (AND-joined terms), terms (a source must mention one), exclude_title (regex), exclude_docs}
RULES: dict[str, dict] = {
    "bp_high": {
        "query": "elevated systolic or diastolic blood pressure above the personal usual in an older adult, hypertension, home blood pressure monitoring",
        "pubmed": ["older adults", "hypertension", "home blood pressure monitoring"],
        "terms": ["hypertension", "blood pressure", "home blood pressure"], "exclude_title": r"hypotens|low blood pressure", "exclude_docs": []},
    "bp_low": {
        "query": "low systolic blood pressure, hypotension in an older adult, early warning score, clinical deterioration, low blood pressure",
        "pubmed": ["older adults", "hypotension OR \"low blood pressure\"", "vital signs OR \"blood pressure monitoring\""],
        "terms": ["hypotension", "low blood pressure", "early warning", "news2", "systolic blood pressure"],
        "exclude_title": r"hypertens", "exclude_docs": ["NICE-NG136"]},
    "hr_high": {
        "query": "raised heart rate, tachycardia, pulse rate in an older adult, early warning score, clinical deterioration",
        "pubmed": ["older adults", "tachycardia OR \"elevated heart rate\"", "vital signs OR \"early warning score\""],
        "terms": ["heart rate", "pulse", "tachycardia", "early warning"], "exclude_title": r"hypertens", "exclude_docs": ["NICE-NG136"]},
    "hr_low": {
        "query": "low heart rate, bradycardia, pulse rate in an older adult, early warning score, clinical deterioration",
        "pubmed": ["older adults", "bradycardia OR \"low heart rate\"", "vital signs OR \"early warning score\""],
        "terms": ["heart rate", "pulse", "bradycardia", "early warning"], "exclude_title": r"hypertens", "exclude_docs": ["NICE-NG136"]},
    "temperature": {
        "query": "raised or low body temperature, fever, temperature as a vital sign in an older adult, early warning score",
        "pubmed": ["older adults", "body temperature OR fever", "vital signs OR \"early warning score\""],
        "terms": ["temperature", "fever", "early warning"], "exclude_title": r"hypertens", "exclude_docs": ["NICE-NG136"]},
    "weight_change": {
        "query": "unintentional weight loss or weight gain in an older adult, nutrition, fluid retention",
        "pubmed": ["older adults", "weight loss OR \"weight change\"", "home monitoring"],
        "terms": ["weight loss", "weight gain", "weight change", "body weight", "nutrition"], "exclude_title": r"hypertens", "exclude_docs": ["NICE-NG136"]},
}


def _hay(src: dict) -> str:
    return f"{src.get('title', '')} {src.get('text', '')}".lower()


def keep(finding_type: str, src: dict) -> bool:
    """True when the source may be shown for this finding type. Types without rules (falls, routine, legacy vitals types) are not restricted here."""
    r = RULES.get(finding_type)
    if r is None:
        return True
    if src.get("doc_id") in r["exclude_docs"] or re.search(r["exclude_title"], str(src.get("title", "")).lower()):
        return False
    return any(re.search(r"\b" + re.escape(t.lower()), _hay(src)) for t in r["terms"])
