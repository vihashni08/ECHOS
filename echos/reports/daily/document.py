"""The daily report as a structured document (SBAR handover + a vitals chart-style table + a prioritised action plan + numbered references).

Everything numeric (tables, trend strip, alerts, background) is built by code from the day's facts; Gemini only contributes the wording of the
assessment / changes / actions / guidance, and only after the validator accepted it. `build_doc` returns plain JSON (stored next to the markdown and
served by the API); `render_markdown` turns the same dict into the .md file. Layout follows clinical practice: identification header, SBAR
(Situation, Background, Assessment, Recommendation), a vital-signs table against the person's own usual, owner + deadline + escalation criteria per action,
and numbered references (Vancouver-style: publisher/journal, year, link)."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from echos.config import load_config
from echos.reports.daily import actions as ACT

VNAME = {"sys": "Systolic blood pressure", "dia": "Diastolic blood pressure", "hr": "Heart rate", "temp": "Body temperature", "weight": "Body weight"}
UNIT = {"sys": "mmHg", "dia": "mmHg", "hr": "bpm", "temp": "°C", "weight": "kg"}
LABEL = {"NORMAL": "Normal", "MONITOR": "Changes noticed", "URGENT": "Needs attention"}
STATUS_TEXT = {"above": "Above usual", "below": "Below usual", "at": "At usual", "within": "Within usual range"}
DISCLAIMER = "Decision support for a caregiver and clinician, not a medical assessment or a diagnosis. Alerts for a fall or a critical reading are sent separately and immediately."


def _g(x) -> str:
    return f"{round(float(x), 1):g}"


def vitals_rows(facts: dict) -> list[dict]:
    band = (load_config().get("weekly") or {}).get("band_sd", 2.0)
    out = []
    for k, nm in VNAME.items():
        v = (facts.get("vitals") or {}).get(k) or {}
        val, base, z = v.get("value"), v.get("baseline"), v.get("z")
        row = {"key": k, "name": nm, "unit": UNIT[k], "value": val, "usual": base, "diff": None, "z": z, "status": "NO_READING", "note": "No reading today"}
        if val is not None and base is not None:
            d = round(float(val) - float(base), 1)
            outside = z is not None and abs(z) >= band
            row.update(diff=d, status=("HIGH" if d > 0 else "LOW") if outside else "WITHIN",
                       note=((STATUS_TEXT["above"] if d > 0 else STATUS_TEXT["below"]) if outside else STATUS_TEXT["within"]) + ("" if z is None else f" ({z:+.1f} SD)"))
        elif val is not None:
            row.update(status="NO_BASELINE", note="Usual not yet established")
        elif base is not None:
            row.update(note="No reading today")
        if val is None and base is None:
            continue
        out.append(row)
    return out


def trend(eng, facts: dict) -> list[dict]:
    """The last 7 days' statuses (oldest first) for the strip at the top of the report."""
    try:
        from echos.reports.daily import facts as F
        return [{"day": d["day"], "status": d["status"]} for d in F.get_last_7_days(eng, facts["patient_id"], facts["day"])]
    except Exception:  # noqa: BLE001 - the strip is optional
        return []


def _publisher(s: dict) -> str:
    """Journal for a PubMed article, issuing body for a guideline; a trailing '(2019)' is dropped (the year is printed separately)."""
    name = (s.get("journal") if s.get("origin") == "pubmed" else None) or s.get("publisher") or s.get("journal") or s.get("source") or ""
    return re.sub(r"\s*\(\d{4}\)", "", str(name)).strip(" ,")


def cite_label(s: dict) -> str:
    """'NICE NG136, 2019, page 12' (local guideline) or 'J Hypertens, 2021' (PubMed): the short form used in the text."""
    pub = _publisher(s)
    yr = f", {s['year']}" if s.get("year") else ""
    pg = f", page {s['page']}" if s.get("page") else ""
    return f"{pub}{yr}{pg}"


def ref_entry(s: dict, n: int) -> dict:
    """One numbered reference. Local guideline chunks cite document + page; PubMed articles cite journal + year + link (abstract only)."""
    return {"n": n, "id": s["id"], "origin": s.get("origin"), "title": str(s.get("title") or "").rstrip(". "), "publisher": _publisher(s),
            "year": s.get("year"), "page": s.get("page"), "url": s.get("url"), "excerpt": s.get("excerpt")}


def build_doc(facts: dict, *, alerts: list, situation: str, background: list, items: dict, sources: list, case: Optional[dict], mode: str,
              data_note: Optional[str], trend_days: list, model: Optional[str] = None) -> dict:
    """items: {assessment, changes, actions, guidance} (already validated, alias mapped back). sources: the cited source dicts, in citation order."""
    refs = [ref_entry(s, n) for n, s in enumerate(sources, 1)]
    num = {r["id"]: r["n"] for r in refs}
    acts = []
    for n, a in enumerate(items.get("actions", []), 1):
        acts.append({**a, "id": a.get("id") or f"A{n}", "priority_label": ACT.PRIORITY_LABEL[a["priority"]], "owner_label": ACT.OWNER_LABEL[a["owner"]],
                     "ref_numbers": [num[c] for c in a.get("cites", []) if c in num]})
    def with_refs(lst):
        return [{**i, "ref_numbers": [num[c] for c in i.get("cites", []) if c in num]} for i in lst]
    return {
        "version": 2,
        "header": {"patient_id": facts["patient_id"], "day": facts["day"], "status": facts["status"], "status_label": LABEL[facts["status"]], "mode": mode, "model": model,
                   "generated": datetime.now().strftime("%Y-%m-%d %H:%M")},
        "situation": situation, "background": background, "alerts": alerts, "trend": trend_days,
        "vitals": vitals_rows(facts), "case": case,
        "assessment": with_refs(items.get("assessment", [])), "changes": with_refs(items.get("changes", [])),
        "actions": acts, "guidance": with_refs(items.get("guidance", [])), "references": refs,
        "data_note": data_note, "disclaimer": DISCLAIMER,
    }


# ------------------------------------------------------------------ markdown
def _cell(x, unit="") -> str:
    return "-" if x is None else f"{_g(x)}{(' ' + unit) if unit else ''}"


def _sign(d) -> str:
    return "-" if d is None else ("0" if d == 0 else f"{d:+g}")


def _tag(i: dict) -> str:
    c = list(dict.fromkeys(i.get("cites", [])))
    return (" [" + ", ".join(c) + "]") if c else ""


def render_markdown(d: dict) -> str:
    h, L = d["header"], []
    L.append(f"# Daily report - {h['patient_id']} - {h['day']}")
    L.append(f"\n**Status: {h['status']}** - {h['status_label']}")
    L.append(f"\n_Generated {h['generated']} - {'written with AI wording, checked by rules' if h['mode'] == 'llm' else 'rule-based template'}_")
    L.append("\n## Situation\n" + d["situation"])
    if d["alerts"]:
        L.append("\n## Alerts\n" + "\n".join(f"- {a}" for a in d["alerts"]))
    if d["vitals"]:
        L.append("\n## Vital signs against this person's own usual\n\n| Measure | Today | Usual | Difference | Status |\n|---|---|---|---|---|")
        for r in d["vitals"]:
            L.append(f"| {r['name']} | {_cell(r['value'], r['unit'])} | {_cell(r['usual'], r['unit'])} | {_sign(r['diff'])} | {r['note']} |")
    if d["assessment"]:
        L.append("\n## Assessment\n" + "\n\n".join(i["text"] + _tag(i) for i in d["assessment"]))
    case = d.get("case")
    if case:
        L.append(f"\n## Case summary\n{case['headline']} _(evidence: {str(case['evidence_level']).lower()}; a rule summary, not a probability)_")
        L.extend(f"- {n}" for n in case.get("next_checks", []))
    L.append("\n## What changed compared with their usual pattern")
    L.append("\n".join(f"- {i['text']}{_tag(i)}" for i in d["changes"]))
    L.append("\n## Plan\n\n| # | When | Who | Action |\n|---|---|---|---|")
    for a in d["actions"]:
        esc = f" **Escalate if:** {a['escalate_if']}." if a.get("escalate_if") else ""
        L.append(f"| {a['id']} | {a['priority_label']} | {a['owner_label']} | {a['text']}{esc}{_tag(a)} |".replace("\n", " "))
    if d["guidance"]:
        L.append("\n## Guidance for the care team\n" + "\n".join(f"- {i['text']}{_tag(i)}" for i in d["guidance"]))
    if d["references"]:
        L.append("\n## References")
        for r in d["references"]:
            where = f", page {r['page']}" if r.get("page") else ""
            link = f" {r['url']}" if r.get("url") else ""
            L.append(f"{r['n']}. [{r['id']}] {r['title']}. {r['publisher'] or ''}{(', ' + str(r['year'])) if r.get('year') else ''}{where}.{link}")
    elif h["status"] != "NORMAL":
        L.append("\n## References\nNo supporting source was retrieved for these findings.")
    if d.get("data_note"):
        L.append("\n## Data note\n" + d["data_note"])
    L.append("\n---\n_" + d["disclaimer"] + "_")
    return "\n".join(L) + "\n"
