"""Key findings paragraph of the weekly PDF: the ONLY LLM-written part of the report.
LLM path (only when asked): one request through the budget manager (cache -> primary -> fallback -> template), output checked by the non-LLM
validator (numbers from the structured data, no diagnostic wording, no medication advice, guidance cites only retrieved sources, ~120 words);
one retry with the validator's errors, then the template. The template is built from the same structured data and is used on any failure."""
from __future__ import annotations

import json
from typing import Optional

from echos.reports.llm.alias import pseudonymise, pseudonymise_back
from echos.reports.llm.llm_budget import GenerationFailed, LLMBudget, make_key
from echos.reports.daily.validator import validate_report
from echos.reports.weekly.weekly_data import narrative_payload

PROMPT_VERSION = "weekly-v3"
SCHEMA = {"type": "object", "required": ["impression", "key_findings", "guidance"], "properties": {
    "impression": {"type": "array", "minItems": 1, "maxItems": 2, "items": {"type": "object", "required": ["text", "cites"], "properties": {
        "text": {"type": "string", "description": "1-3 sentences: the overall picture of the week and what matters most for the clinician; no diagnosis"},
        "cites": {"type": "array", "items": {"type": "string"}}}}},
    "key_findings": {"type": "array", "minItems": 3, "maxItems": 6, "items": {"type": "object", "required": ["text", "cites"], "properties": {
        "text": {"type": "string", "description": "1-2 factual sentences with the exact values, dates and the comparison with the personal baseline"},
        "cites": {"type": "array", "items": {"type": "string"}}}}},
    "guidance": {"type": "array", "maxItems": 2, "items": {"type": "object", "required": ["text", "cites"], "properties": {
        "text": {"type": "string", "description": "one paraphrased statement from a listed source"},
        "cites": {"type": "array", "items": {"type": "string"}}}}}}}
SYSTEM = ("You write the 'Impression' and 'Key findings' of a weekly report for a clinician, in the style of a clinical handover, plain factual language. Use ONLY the structured data "
          "in the JSON. Copy numbers exactly; never invent one. Describe vitals against this person's own baseline. Never mention any probability or score "
          "except the fall probability given for a fall. Say 'activity pattern differs from usual', not more. Never state a diagnosis (no 'diagnosis', "
          "'you have', 'suffering from') and never give advice about medicines or doses. Guidance statements must paraphrase a listed source and cite its id. Reply with JSON only.")
ASK = ('Return {{"impression":[{{"text":str,"cites":[]}}],"key_findings":[{{"text":str,"cites":[]}}],"guidance":[{{"text":str,"cites":[source ids]}}]}} (exactly these three keys, no others). "impression": 1 or 2 items, together 2 to 4 sentences: the overall picture of the week, the most important finding and whether anything reached an urgent limit; use only numbers in the data. '
       'Write in the style of a clinical handover (situation, background, assessment): "key_findings" is 4 to 6 items of 1-2 sentences each, about 120 to 200 words in all. '
       'Cover, in this order: (1) the week at a glance: days with data and Normal / Monitor / Urgent counts; (2) each vital sign: weekly mean, personal baseline, number of readings outside the band, with exact values; '
       '(3) falls (day, time, whether inactivity followed) or that none were detected; (4) days the activity pattern differed from usual, with dates; (5) data completeness and any gap, stating that missing readings are not evidence of normal values. "guidance": 0 to 2 short paraphrased statements, only from '
       'the sources below, each citing its id; [] if there are none.\n\nDATA:\n{data}\n\nSOURCES:\n{sources}')


def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def template_items(d: dict) -> dict:
    c, comp, vs = d["status_counts"], d["completeness"], d["vitals_summary"]
    s = [f"Week {d['week_start']} to {d['week_end']}: {comp['days_with_data']} of {d['days_in_week']} days had data, with {c['NORMAL']} Normal, "
         f"{c['MONITOR']} Monitor and {c['URGENT']} Urgent days" + (f" ({_plural(c['DATA_GAP'], 'day')} with a data gap)" if c["DATA_GAP"] else "")
         + (f"; {_plural(c['NOT_PROCESSED'], 'day')} with data not yet processed by ECHOS." if c.get("NOT_PROCESSED") else ".")]
    for k in ("sys", "dia", "hr", "temp", "weight"):
        v = vs[k]
        if not v["n_readings"]:
            continue
        if not v["baseline_established"]:
            if k in ("sys", "hr"):
                s.append(f"{v['name']} averaged {v['mean']:g} {v['unit']}; {v['note']}.")
        elif k in ("sys", "hr") or v["n_flagged"]:
            n = v["n_flagged"]
            s.append(f"{v['name']} averaged {v['mean']:g} {v['unit']} against a personal baseline of {v['baseline']:g}; "
                     f"{n} of {v['n_with_baseline']} readings {'was' if n == 1 else 'were'} outside the personal band.")
    if d["falls"]:
        f = ", ".join(f"{x['day']} {x['time']} (probability {x['probability_text']}, inactivity {'yes' if x['inactivity'] else 'no'})" for x in d["falls"][:3])
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
    return {"impression": [{"text": template_impression(d), "cites": []}], "key_findings": [{"text": x, "cites": []} for x in s], "guidance": []}


def template_impression(d: dict) -> str:
    """The overall picture in two or three sentences: the most important finding of the week first (fall > critical reading > vitals alert > routine change), then what was quiet."""
    c, comp = d["status_counts"], d["completeness"]
    crit = [a for a in d.get("vitals_alerts", []) if a["kind"] == "critical"]
    if d["falls"]:
        f = sorted(d["falls"], key=lambda x: (x["day"], x["time"] or ""))[-1]
        when = f"{f['day']} at {f['time']}" if f.get("time") else f["day"]
        lead = f"The most important finding is {_plural(len(d['falls']), 'possible fall')} this week (latest {when}" + (", followed by little movement)." if f.get("inactivity") else ").")
    elif crit:
        lead = f"The most important finding is {_plural(len(crit), 'reading')} in the critical range (latest {sorted(a['day'] for a in crit)[-1]})."
    elif d.get("vitals_alerts"):
        lead = f"{_plural(len(d['vitals_alerts']), 'vitals alert')} differed from this person's own usual, none in the critical range."
    elif d.get("unusual_days"):
        lead = f"The activity pattern differed from usual on {_plural(len(d['unusual_days']), 'day')}; there were no vitals alerts and no possible falls."
    else:
        lead = "Nothing differed from this person's own usual pattern this week."
    tail = f" {c['MONITOR']} Monitor and {c['URGENT']} Urgent day{'' if c['MONITOR'] + c['URGENT'] == 1 else 's'} of {comp['days_with_data']} with data."
    gap = " Missing readings are not evidence of normal values." if comp["days_with_vitals"] < comp["days_with_data"] or comp["days_with_activity"] < comp["days_with_data"] else ""
    return lead + tail + gap


def _check(out, d: dict, sources: list) -> dict:
    guid = {"section": "guidance", "ids": [x["id"] for x in sources], "texts": {x["id"]: x["text"] for x in sources}}
    v = validate_report(out, narrative_payload(d), None, [x["id"] for x in sources], [], guid)
    if isinstance(out, dict):
        n = sum(len(str(i.get("text", "")).split()) for k in ("key_findings", "guidance") for i in out.get(k, []) if isinstance(i, dict))
        if not 50 <= n <= 220:
            v = {**v, "ok": False, "errors": v["errors"] + [f"about 120 words expected, got {n}"], "codes": sorted(set(v.get("codes", [])) | {"length"}),
                 "findings": v.get("findings", []) + [{"rule": "length", "detail": f"{n} words, 50 to 220 expected"}]}
    return v


def render(items: dict) -> str:
    kf = " ".join(i["text"].strip() for k in ("impression", "key_findings") for i in items.get(k, []))
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
                fb, vv, attempts = "", None, []
                for _ in range(2):
                    r = ctx.step(SYSTEM, [{"role": "user", "text": ASK.format(data=json.dumps(pseudonymise(narrative_payload(d), d["patient_id"]), default=str), sources=src_json) + fb}], None, SCHEMA)
                    t = r.text.strip()
                    if t.startswith("```"):
                        t = t.strip("`").removeprefix("json").strip()
                    try:
                        out = json.loads(t)
                        vv = _check(out, d, sources)
                    except ValueError:
                        vv = {"ok": False, "errors": ["reply was not valid JSON"], "codes": ["json_invalid"], "findings": [{"rule": "json_invalid", "detail": "reply was not valid JSON"}]}
                    if vv["ok"]:
                        return {"items": {k: out.get(k, []) for k in ("impression", "key_findings", "guidance")}, "validation": vv}
                    attempts.append({"raw": r.text, "validator": vv})
                    fb = "\n\nYour previous reply was rejected for: " + "; ".join(vv["errors"][:6]) + ". Fix these and reply again."
                raise GenerationFailed("validation_failed", {**vv, "attempts": attempts})
            res = budget.run(attempt, key=key, revalidate=lambda c: _check(c["items"], d, sources)["ok"], prompt_version=PROMPT_VERSION)
            prov.update(requests=res.requests, tokens={"prompt": res.prompt_tokens, "output": res.output_tokens}, models_tried=res.models_tried, events=res.events)
            if res.ok:
                items = pseudonymise_back(res.value["items"], d["patient_id"])         # alias -> patient id, in code only
                v = _check(res.value["items"], d, sources)
                prov.update(model=res.model, mode="llm", cache_hit=res.cache_hit, fallback_reason=None)
            else:
                prov["fallback_reason"] = res.fallback_reason
                if res.validation:
                    prov["validator_failure"] = res.validation
        except Exception as ex:   # noqa: BLE001 - the template is always available
            prov["fallback_reason"] = f"error ({type(ex).__name__})"
    elif use_llm:
        prov["fallback_reason"] = "no LLM configured"
    prov["validator"] = v
    return {"text": render(items), "mode": prov["mode"], "items": items, "validation": v, "provenance": prov}
