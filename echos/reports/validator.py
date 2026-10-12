"""Non-LLM validator. Checks a report-writer output (JSON of {section: [{text, cites}]}) against its input payload.

Rules: (1) every number in the text appears in the input (citation ids like [KB-65], [PM-123], F1 are not numbers); (2) no diagnostic wording,
no medication advice, no quoting of the agitation score; (3) every item cites known evidence ids and every required flag is cited by at least one
item; (4) when `expected_status` is given, the output's `status` must equal the arbiter's status text exactly; (5) every guidance statement cites
at least one source that was actually retrieved, and paraphrases it.
"""
from __future__ import annotations

import re

NUM = re.compile(r"\d+(?:\.\d+)?")
BANNED = [r"diagnos", r"\byou have\b", r"\bsuffer(s|ing)? from\b", r"\bis suffering\b", r"\bhas developed\b",
          r"\bconfirm(s|ed)? (that )?(he|she|they|the patient) has\b",
          # medication advice: ECHOS never tells anyone to start / stop / change a medicine
          r"\b(take|taking|start|stop|skip|increase|decrease|reduce|adjust|change|double|restart)\b[^.]{0,40}\b(medication|medicine|medicines|medications|dose|doses|dosage|tablets?|pills?|drugs?|antihypertensives?|sedatives?)\b",
          r"\bprescri(be|bed|ption)\b", r"\bdosage\b", r"\b\d+\s?(mg|mcg|ml)\b",
          # the agitation score has modest skill and is not a probability: never quote it
          r"\bagitation (probability|risk|score|likelihood)\b", r"\bprobability of agitation\b"]
CITE_TOKEN = re.compile(r"\[?\b(?:KB|PM)-\d+\b\]?|\b[FE]\d+\b")


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


def _ngrams(text: str, n: int) -> set:
    w = re.findall(r"[a-z0-9']+", text.lower())
    return {tuple(w[i:i + n]) for i in range(len(w) - n + 1)}


QUOTE_WORDS = 10  # a run of this many consecutive words copied from a retrieved chunk counts as a quote, not a paraphrase


def check_guidance(output: dict, guidance: dict, errors: list):
    """guidance = {"section": "guidance", "ids": retrieved chunk ids, "texts": {id: chunk text}}.
    Every guidance statement must cite >=1 id, every cited id must be in the retrieved set, and statements must paraphrase."""
    ids = set(guidance["ids"])
    src = {i: _ngrams(t, QUOTE_WORDS) for i, t in guidance.get("texts", {}).items()}
    for it in output.get(guidance["section"], []) or []:
        if not isinstance(it, dict):
            continue
        cites = [c for c in (it.get("cites") or []) if isinstance(c, str)]
        if not cites:
            errors.append("guidance statement without a citation")
        for c in cites:
            if c not in ids:
                errors.append(f"guidance cites {c}, which was not retrieved")
        grams = _ngrams(str(it.get("text", "")), QUOTE_WORDS)
        if any(grams & g for g in src.values()):
            errors.append("guidance quotes the source verbatim (paraphrase required)")


def validate_report(output: dict, payload: dict, required_ids=None, valid_ids=None, cite_sections=(), guidance=None,
                    expected_status=None) -> dict:
    """Return {ok, errors}. required_ids: ids that must be cited; valid_ids: ids that may be cited;
    cite_sections: sections whose every item must cite at least one id; guidance: see check_guidance."""
    errors = []
    if not isinstance(output, dict):
        return {"ok": False, "errors": ["output is not a JSON object"]}
    if expected_status is not None and output.get("status") != expected_status:
        errors.append(f"status text {str(output.get('status'))[:30]!r} does not match the arbiter's status {expected_status!r}")
    allowed = input_numbers(payload)
    cites_text = {c for it in _items(output) for c in (it.get("cites") or []) if isinstance(c, str)}
    for t in _texts({k: [{"text": it.get("text", "")} for it in v if isinstance(it, dict)]
                     for k, v in output.items() if isinstance(v, list)}):
        for m in NUM.findall(CITE_TOKEN.sub(" ", t)):
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
    if guidance:
        check_guidance(output, guidance, errors)
    for rid in (required_ids or []):
        if rid not in cites_text:
            errors.append(f"flag {rid} is not cited")
    return {"ok": not errors, "errors": sorted(set(errors))[:12]}
