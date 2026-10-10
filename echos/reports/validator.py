"""Non-LLM validator. Checks a report-writer output (JSON of {section: [{text, cites}]}) against its input payload.

Rules: (1) every number in the text appears in the input; (2) no diagnostic wording; (3) every item cites known
evidence ids and every required flag is cited by at least one item.
"""
from __future__ import annotations

import re

NUM = re.compile(r"\d+(?:\.\d+)?")
BANNED = [r"diagnos", r"\byou have\b", r"\bsuffer(s|ing)? from\b", r"\bis suffering\b", r"\bhas developed\b",
          r"\bconfirm(s|ed)? (that )?(he|she|they|the patient) has\b"]


def _walk(x, out: list):
    if isinstance(x, bool) or x is None:
        return
    if isinstance(x, (int, float)):
        out.append(float(x))
    elif isinstance(x, str):
        out.extend(float(m) for m in NUM.findall(x))
    elif isinstance(x, dict):
        for k, v in x.items():
            _walk(k, out)
            _walk(v, out)
    elif isinstance(x, (list, tuple)):
        for v in x:
            _walk(v, out)


def input_numbers(payload) -> set:
    raw: list = []
    _walk(payload, raw)
    allowed = set()
    for v in raw:
        for nd in (0, 1, 2):
            allowed.add(round(v, nd))
        allowed.add(v)
    return allowed


def _items(output: dict) -> list:
    return [it for v in output.values() if isinstance(v, list) for it in v if isinstance(it, dict)]


def _texts(output) -> list:
    out: list = []
    if isinstance(output, str):
        return [output]
    if isinstance(output, dict):
        for v in output.values():
            out.extend(_texts(v))
    elif isinstance(output, list):
        for v in output:
            out.extend(_texts(v))
    return out


def validate_report(output: dict, payload: dict, required_ids=None, valid_ids=None, cite_sections=()) -> dict:
    """Return {ok, errors}. required_ids: ids that must be cited; valid_ids: ids that may be cited;
    cite_sections: sections whose every item must cite at least one id."""
    errors = []
    if not isinstance(output, dict):
        return {"ok": False, "errors": ["output is not a JSON object"]}
    allowed = input_numbers(payload)
    cites_text = {c for it in _items(output) for c in (it.get("cites") or []) if isinstance(c, str)}
    for t in _texts({k: [{"text": it.get("text", "")} for it in v if isinstance(it, dict)]
                     for k, v in output.items() if isinstance(v, list)}):
        for m in NUM.findall(t):
            if round(float(m), 2) not in allowed and float(m) not in allowed:
                errors.append(f"number {m} not in input")
        for pat in BANNED:
            if re.search(pat, t, re.I):
                errors.append(f"diagnostic wording: /{pat}/")
    valid = set(valid_ids or [])
    for k, v in output.items():
        if not isinstance(v, list):
            continue
        for it in v:
            if not isinstance(it, dict) or not str(it.get("text", "")).strip():
                errors.append(f"{k}: item without text")
                continue
            bad = [c for c in (it.get("cites") or []) if valid and c not in valid]
            if bad:
                errors.append(f"{k}: unknown evidence id {bad}")
    for k in cite_sections:
        for it in output.get(k, []) or []:
            if not it.get("cites"):
                errors.append(f"{k}: item cites no evidence")
    for rid in (required_ids or []):
        if rid not in cites_text:
            errors.append(f"flag {rid} is not cited")
    return {"ok": not errors, "errors": sorted(set(errors))[:12]}
