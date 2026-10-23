"""Non-LLM validator for the Evidence agent's text. A generated explanation is shown only if every check passes; otherwise the fixed template is used.

Checks:
  json         the reply is a JSON object with a string `explanation` and a list `citations`
  citations    at least one citation when passages were retrieved; every cited id was actually retrieved; every inline [id] marker is one of them
  numbers      every number in the explanation appears in the structured facts or in a cited passage (no invented figures)
  wording      no diagnosis, medication, dose or treatment-start/stop wording
  length       at most MAX_CHARS characters
The validator proves the text is traceable to the inputs; it cannot prove the retrieved guideline is the right one for the patient."""
from __future__ import annotations

import json
import re

MAX_CHARS = 700
BANNED = re.compile(r"\b(diagnosed with|diagnosis of|is diagnos\w*|prescrib\w*|medicat\w*|antipsychotic\w*|\d+\s?(mg|mcg|ml)\b|dose\w*|start (him|her|them)|stop (his|her|their)|has (dementia|hypertension|sepsis))", re.I)
NUM = re.compile(r"\d+(?:\.\d+)?")
MARK = re.compile(r"\[([A-Za-z0-9_.-]+-p\d+-\d+)\]")


def _numbers(s: str) -> set:
    return {n.rstrip("0").rstrip(".") if "." in n else n for n in NUM.findall(s)}


def parse(raw: str) -> dict | None:
    try:
        d = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def validate(raw: str, facts_text: str, passages: list[dict]) -> tuple[bool, list[str], dict | None]:
    """Returns (ok, problems, parsed). `passages` = [{id, text}, ...] as given to the model."""
    d = parse(raw)
    if d is None or not isinstance(d.get("explanation"), str) or not isinstance(d.get("citations"), list):
        return False, ["json: expected an object with `explanation` (string) and `citations` (list)"], None
    exp, cites = d["explanation"].strip(), [str(c) for c in d["citations"]]
    ids = {p["id"] for p in passages}
    problems = []
    if not exp:
        problems.append("length: empty explanation")
    if len(exp) > MAX_CHARS:
        problems.append(f"length: {len(exp)} > {MAX_CHARS} characters")
    if ids and not cites:
        problems.append("citations: passages were retrieved but none is cited")
    bad = [c for c in cites if c not in ids]
    if bad:
        problems.append(f"citations: not retrieved: {', '.join(bad)}")
    stray = [m for m in MARK.findall(exp) if m not in ids]
    if stray:
        problems.append(f"citations: inline marker not retrieved: {', '.join(stray)}")
    if not ids and cites:
        problems.append("citations: cites a source although none was retrieved")
    allowed = _numbers(facts_text) | _numbers(" ".join(p["text"] for p in passages if p["id"] in cites))
    allowed |= {n for n in _numbers(" ".join(MARK.findall(exp))) | {n for c in cites for n in _numbers(c)}}   # page / chunk numbers inside ids
    extra = sorted(_numbers(MARK.sub("", exp)) - allowed)
    if extra:
        problems.append(f"numbers: not in the facts or cited passages: {', '.join(extra)}")
    m = BANNED.search(exp)
    if m:
        problems.append(f"wording: '{m.group(0)}'")
    return (not problems), problems, d
