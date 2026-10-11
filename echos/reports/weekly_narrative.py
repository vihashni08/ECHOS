"""Key findings paragraph of the weekly PDF: the ONLY LLM-written part of the report.
LLM path (only when asked): one request through the budget manager (cache -> primary -> fallback -> template), output checked by the non-LLM
validator (numbers from the structured data, no diagnostic wording, no medication advice, guidance cites only retrieved sources, ~120 words);
one retry with the validator's errors, then the template. The template is built from the same structured data and is used on any failure."""
from __future__ import annotations

import json
from typing import Optional

from echos.reports.llm_budget import GenerationFailed, LLMBudget, make_key
from echos.reports.validator import validate_report
from echos.reports.weekly_data import narrative_payload

PROMPT_VERSION = "weekly-v1"
SYSTEM = ("You write the 'Key findings' paragraph of a weekly report for a clinician, about 120 words, plain factual language. Use ONLY the structured data "
          "in the JSON. Copy numbers exactly; never invent one. Describe vitals against this person's own baseline. Never mention any probability or score "
          "except the fall probability given for a fall. Say 'activity pattern differs from usual', not more. Never state a diagnosis (no 'diagnosis', "
          "'you have', 'suffering from') and never give advice about medicines or doses. Guidance statements must paraphrase a listed source and cite its id. Reply with JSON only.")
ASK = ('Return {{"key_findings":[{{"text":str,"cites":[]}}],"guidance":[{{"text":str,"cites":[source ids]}}]}}. "key_findings": 3 to 5 sentences covering '
       'day statuses, vitals against baseline, falls, unusual-activity days and data completeness. "guidance": 0 to 2 short paraphrased statements, only from '
       'the sources below, each citing its id; [] if there are none.\n\nDATA:\n{data}\n\nSOURCES:\n{sources}')


def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def template_items(d: dict) -> dict:
    c, comp, vs = d["status_counts"], d["completeness"], d["vitals_summary"]
    s = [f"Week {d['week_start']} to {d['week_end']}: {comp['days_with_data']} of {d['days_in_week']} days had data, with {c['NORMAL']} Normal, "
         f"{c['MONITOR']} Monitor and {c['URGENT']} Urgent days" + (f" ({_plural(c['DATA_GAP'], 'day')} with a data gap)." if c["DATA_GAP"] else ".")]
    for k in ("sys", "dia", "hr", "temp", "weight"):
        v = vs[k]
        if v["n_readings"] and (k in ("sys", "hr") or v["n_flagged"]):
            base = f" against a personal baseline of {v['baseline']:g}" if v["baseline"] is not None else " (no personal baseline yet)"
            s.append(f"{v['name']} averaged {v['mean']:g} {v['unit']}{base}; {v['n_flagged']} of {v['n_readings']} readings were outside the personal band.")
    if d["falls"]:
        f = ", ".join(f"{x['day']} {x['time']} (probability {x['probability']:g}, inactivity {'yes' if x['inactivity'] else 'no'})" for x in d["falls"][:3])
        s.append(f"{_plural(len(d['falls']), 'possible fall')}: {f}.")
    else:
        s.append("No possible falls were detected.")
    if d["unusual_days"]:
        s.append(f"The activity pattern differed from usual on {_plural(len(d['unusual_days']), 'day')}: {', '.join(u['day'] for u in d['unusual_days'])}.")
    else:
        s.append("No day had an unusual activity pattern.")
    s.append(f"Vitals readings were recorded on {comp['days_with_vitals']} of {d['days_in_week']} days and room-sensor activity on {comp['days_with_activity']} "
             f"of {d['days_in_week']} days; {_plural(comp['sensor_gap_days'], 'sensor-gap day')}, {comp['invalid_values_removed']} invalid values removed.")
    if d["partial_week_note"]:
        s.append(d["partial_week_note"])
    return {"key_findings": [{"text": " ".join(s), "cites": []}], "guidance": []}


def _check(out, d: dict, sources: list) -> dict:
    guid = {"section": "guidance", "ids": [x["id"] for x in sources], "texts": {x["id"]: x["text"] for x in sources}}
    v = validate_report(out, narrative_payload(d), None, [x["id"] for x in sources], [], guid)
    if isinstance(out, dict):
        n = sum(len(str(i.get("text", "")).split()) for k in ("key_findings", "guidance") for i in out.get(k, []) if isinstance(i, dict))
        if not 50 <= n <= 220:
            v = {"ok": False, "errors": v["errors"] + [f"about 120 words expected, got {n}"]}
    return v


def render(items: dict) -> str:
    kf = " ".join(i["text"].strip() for i in items.get("key_findings", []))
    gd = " ".join(f"{i['text'].strip()} [{', '.join(i.get('cites', []))}]" for i in items.get("guidance", []))
    return (kf + (" " + gd if gd else "")).strip()


def make_narrative(d: dict, sources: list, budget: Optional[LLMBudget] = None, use_llm: bool = False) -> dict:
    """-> {text, mode, items, validation, provenance}. Never raises: any failure gives the template."""
    items = template_items(d)
    prov = {"model": "template", "mode": "template", "cache_hit": False, "tools": [], "requests": 0, "tokens": {"prompt": 0, "output": 0}, "models_tried": [],
            "events": [], "fallback_reason": None if use_llm else "LLM narrative not requested (template)"}
    v = _check(items, d, sources)
    if use_llm and budget is not None:
        try:
            src_json = json.dumps([{"id": x["id"], "title": x["title"], "text": x["text"][:600]} for x in sources])
            key = make_key(PROMPT_VERSION, {"data": narrative_payload(d), "sources": [x["id"] for x in sources]})

            def attempt(ctx):
                fb, vv = "", None
                for _ in range(2):
                    r = ctx.step(SYSTEM, [{"role": "user", "text": ASK.format(data=json.dumps(narrative_payload(d), default=str), sources=src_json) + fb}], None)
                    t = r.text.strip()
                    if t.startswith("```"):
                        t = t.strip("`").removeprefix("json").strip()
                    out = json.loads(t)
                    vv = _check(out, d, sources)
                    if vv["ok"]:
                        return {"items": {k: out.get(k, []) for k in ("key_findings", "guidance")}, "validation": vv}
                    fb = "\n\nYour previous reply was rejected for: " + "; ".join(vv["errors"][:6]) + ". Fix these and reply again."
                raise GenerationFailed("validation_failed", vv)
            res = budget.run(attempt, key=key, revalidate=lambda c: _check(c["items"], d, sources)["ok"], prompt_version=PROMPT_VERSION)
            prov.update(requests=res.requests, tokens={"prompt": res.prompt_tokens, "output": res.output_tokens}, models_tried=res.models_tried, events=res.events)
            if res.ok:
                items, v = res.value["items"], _check(res.value["items"], d, sources)
                prov.update(model=res.model, mode="llm", cache_hit=res.cache_hit, fallback_reason=None)
            else:
                prov["fallback_reason"] = res.fallback_reason
        except Exception as ex:   # noqa: BLE001 - the template is always available
            prov["fallback_reason"] = f"error ({type(ex).__name__})"
    elif use_llm:
        prov["fallback_reason"] = "no LLM configured"
    prov["validator"] = v
    return {"text": render(items), "mode": prov["mode"], "items": items, "validation": v, "provenance": prov}
