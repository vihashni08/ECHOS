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


PCT = re.compile(r"\s?(%|percent\b|per cent\b)", re.I)


def _pct(text: str, m) -> bool:
    return bool(PCT.match(text, m.end()))


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


def _rule(msg: str) -> str:
    """Stable rule code for an error string (provenance / live-check output)."""
    for pat, code in ((r"^number ", "number_not_in_input"), (r"^diagnostic wording: /.*(diagnos|you have|suffer|developed|confirm)", "diagnostic_wording"),
                      (r"^diagnostic wording: /.*(agitation|probability)", "score_quoted"), (r"^diagnostic wording", "medication_advice"),
                      (r"without a citation", "guidance_uncited"), (r"was not retrieved", "guidance_not_retrieved"),
                      (r"verbatim", "guidance_quoted"), (r"unknown evidence id", "unknown_cite_id"), (r"cites no evidence", "item_uncited"),
                      (r"item without text", "item_without_text"), (r"quotes none of its recorded values", "not_specific"), (r"^flag .* not cited", "flag_not_cited"), (r"status text", "status_mismatch"),
                      (r"^schema mismatch", "schema_mismatch"), (r"not a JSON object", "not_json_object"), (r"monitor needs", "monitor_count"), (r"words expected", "length"),
                      (r"not valid JSON", "json_invalid")):
        if re.search(pat, msg):
            return code
    return "other"


def error_codes(errors) -> list:
    return sorted({_rule(e) for e in errors})


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


def check_specific(output: dict, flags: list, errors: list, where: dict):
    """A `changes` sentence about a flag that carries recorded values (evidence.specifics: which vital / routine measure, its value, the person's own usual)
    must quote at least one of those numbers. 'The activity pattern differs from usual.' with no value is vague, not wrong, and is rejected so the one
    retry (or the template, which always quotes them) produces a specific sentence."""
    import re
    nums = {}
    for f in flags or []:
        sp = (f.get("evidence") or {}).get("specifics") or []
        n = {round(float(m), 1) for s_ in sp for m in re.findall(r"(?<![\w.])\d+(?:\.\d+)?", s_)}
        if n:
            nums[f["id"]] = n
    for i, it in enumerate(output.get("changes", []) or []):
        if not isinstance(it, dict):
            continue
        cited = [c for c in (it.get("cites") or []) if c in nums]
        if not cited:
            continue
        have = {round(float(m), 1) for m in re.findall(r"(?<![\w.])\d+(?:\.\d+)?", CITE_TOKEN.sub(" ", str(it.get("text", ""))))}
        if not any(have & nums[c] for c in cited):
            e = f"change for {cited[0]} quotes none of its recorded values"
            errors.append(e)
            where.setdefault(e, f"changes[{i}]")


def validate_report(output: dict, payload: dict, required_ids=None, valid_ids=None, cite_sections=(), guidance=None,
                    expected_status=None, flags=None) -> dict:
    """Return {ok, errors}. required_ids: ids that must be cited; valid_ids: ids that may be cited;
    cite_sections: sections whose every item must cite at least one id; guidance: see check_guidance."""
    errors = []
    if not isinstance(output, dict):
        return {"ok": False, "errors": ["output is not a JSON object"], "codes": ["not_json_object"],
                "findings": [{"rule": "not_json_object", "detail": "output is not a JSON object"}]}
    if expected_status is not None and output.get("status") != expected_status:
        errors.append(f"status text {str(output.get('status'))[:30]!r} does not match the arbiter's status {expected_status!r}")
    allowed = input_numbers(payload)
    cites_text = {c for it in _items(output) for c in (it.get("cites") or []) if isinstance(c, str)}
    where = {}      # error string -> "section[item]" of the first offender (never the text itself)
    for k, v in output.items():
        if not isinstance(v, list):
            continue
        for i, it in enumerate(v):
            if not isinstance(it, dict):
                continue
            t = str(it.get("text", ""))
            plain = CITE_TOKEN.sub(" ", t)
            for mt in NUM.finditer(plain):
                m = mt.group()
                if _pct(plain, mt) and round(float(m) / 100, 2) in allowed:      # "10%" for a stored fraction 0.104 (rounded like any other number)
                    continue
                if round(float(m), 2) not in allowed and float(m) not in allowed:
                    errors.append(f"number {m} not in input")
                    where.setdefault(f"number {m} not in input", f"{k}[{i}]")
            for pat in BANNED:
                if re.search(pat, t, re.I):
                    errors.append(f"diagnostic wording: /{pat}/")
                    where.setdefault(f"diagnostic wording: /{pat}/", f"{k}[{i}]")
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
    check_specific(output, flags, errors, where)
    for rid in (required_ids or []):
        if rid not in cites_text:
            errors.append(f"flag {rid} is not cited")
    errs = sorted(set(errors))[:12]
    return {"ok": not errors, "errors": errs, "codes": error_codes(errs),
            "findings": [{"rule": _rule(e), "detail": e + (f" at {where[e]}" if e in where else "")} for e in errs]}
