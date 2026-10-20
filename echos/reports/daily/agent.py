"""Agent 4: Report agent. Daily caregiver report + weekly clinician report.

Pipeline (tools: get_last_7_days, get_guideline, validate_report): structured facts -> [LLM wording] -> non-LLM
validator -> retry once with the validator's errors -> Jinja2 template fallback. The LLM only phrases items from the
input JSON; status, alerts, data notes and all tables/charts are produced by code. The LLM never decides emergencies.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import time
import weakref
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

import jinja2
import pandas as pd

from echos.config import ROOT, load_config
from echos.orchestrator import state as S
from echos.reports.daily import facts as F
from echos.reports.llm.alias import ALIAS, pseudonymise, pseudonymise_back  # noqa: F401
from echos.reports.daily.vitals_why import VITALS_TYPES
from echos.knowledge.retrieve import retrieve


def _safe_retrieve(*a, **k) -> list:
    """RAG down (index missing, embedding failure): reports go on without guideline guidance, never crash the case."""
    try:
        return retrieve(*a, **k)
    except Exception as ex:  # noqa: BLE001
        logging.getLogger("echos.llm.fallback").warning("RAG unavailable (%s): report without guidance", type(ex).__name__)
        return []
from echos.reports.llm.llm import LLMAPIError
from echos.reports.llm.llm_budget import Backend, Cache, GenerationFailed, LLMBudget, make_key
from echos.reports.retrieval import GATED_STATUSES, GatedRetriever
from echos.reports import evidence as EV
from echos.reports.daily import document as DOC
from echos.reports.weekly.weekly_data import fmt_prob
from echos.reports.daily import schema as SCHEMA
from echos.reports.daily.validator import validate_report

TPL = jinja2.Environment(loader=jinja2.FileSystemLoader(str(Path(__file__).parent.parent / "templates")), autoescape=False,
                         trim_blocks=True, lstrip_blocks=True)
HTML_TPL = jinja2.Environment(loader=jinja2.FileSystemLoader(str(Path(__file__).parent.parent / "templates")), autoescape=True,
                              trim_blocks=True, lstrip_blocks=True)
LABEL = {"NORMAL": "Normal", "MONITOR": "Changes noticed", "URGENT": "Needs attention"}
VITAL_NAME = {"sys": "Systolic blood pressure", "dia": "Diastolic blood pressure", "hr": "Heart rate",
              "temp": "Body temperature", "weight": "Body weight"}
UNIT = {"sys": "mmHg", "dia": "mmHg", "hr": "bpm", "temp": "°C", "weight": "kg"}
FEAT_NAME = {"total_events": "overall activity", "night_events": "night-time activity",
             "night_bathroom": "night-time bathroom visits", "night_door": "night-time door use",
             "room_transitions": "moving between rooms"}

SYSTEM = ("You write short, plain-language notes for a home-care report. Use ONLY the facts in the JSON the user "
          "sends. Never invent or alter a number; copy numbers exactly as they appear. Never state or imply a medical "
          "diagnosis; do not use the words 'diagnosis' or 'you have'. Every item that mentions a finding must list the "
          "flag/evidence ids it relies on in `cites`. Reply with JSON only, in exactly the requested shape.")
DAILY_ASK = ('Write for a family caregiver. Return {"changes":[{"text":str,"cites":[flag ids]}],'
             '"monitor":[{"text":str,"cites":[flag ids]}]}. "changes": what differs from this person\'s own usual pattern '
             '(one item per flag; if there are no flags write one item saying everything looked normal, cites []). '
             '"monitor": 2 or 3 simple things to watch today. Every flag id in the JSON must be cited at least once.')
WEEKLY_ASK = ('Write for a clinician. Return {"highlights":[{"text":str,"cites":[evidence ids]}],"guidance":[{"text":str,"cites":[KB ids]}]}. '
              '"highlights": 3 to 6 factual highlights of the week (vitals vs baseline, alerts, routine, data completeness); each about a vital must name the measure (for example "systolic blood pressure"), its value or range for the week and the days; when an evidence item has "specifics" quote at least one of its numbers; each must cite at '
              'least one evidence id (E..) and every evidence id must be cited at least once. "guidance": at most one short statement per '
              'finding type, using ONLY the retrieved chunks in "guidelines" (ids like KB-3). PARAPHRASE them in your own words (never copy '
              'more than a few words in a row), cite the KB id(s) used in every guidance statement, and give no diagnosis. '
              'If "guidelines" is empty return "guidance": [].')


# ------------------------------------------------------------------ daily report: tool loop
PROMPT_VERSION = "daily-v7"
MAX_TOOL_STEPS = 3     # tool calls in the one tool loop; the next request is the final answer with tools switched off.
                       # The sources and the last 7 days are PREFETCHED by code into the first prompt, so a typical report is ONE request;
                       # worst case = MAX_TOOL_STEPS tool requests + 1 final + 1 validator retry (no tools).
DAILY_SYSTEM = ("You write the narrative parts of a home-care daily report for a family caregiver, in the style of a clinical handover (SBAR). The structured facts are in the message; "
                "the tools can fetch more. Use ONLY facts in the message or returned by the tools. Never invent or alter a number; copy numbers exactly. Describe vitals as above or below "
                "this person's usual range. Never quote any probability or score; for activity name the measure (for example night-time bathroom visits) with its number and the person's usual, never just 'the activity pattern differs'. Never state or "
                "imply a medical diagnosis (do not use 'diagnosis', 'you have', 'suffering from'). Never give advice about medicines or doses. "
                "Any guidance statement must paraphrase a source in `sources` (or returned by retrieve_guidance) and cite its id (like KB-65 or PM-12345678). Reply "
                "with JSON only.")
DAILY_ASK = ('Today is {day}; the rules decided status {status} (never change it). Flag ids to cover: {flags}. The PATIENT CONTEXT below holds the vitals against the '
             'person\'s own baseline, the routine, the flags, "proposed_plan" (the rule-based starting plan), "escalation_limits" (the only limits you may quote), "sources" (guideline and PubMed items already retrieved for these findings) and "recent_days". '
             'You may call retrieve_guidance(finding_type, details) for another finding (finding_type one of {types}), but usually the sources are enough. Then reply with ONE JSON object that follows exactly this JSON Schema '
             '(every list item is an object, never a bare string; no other keys): {shape}. "status" must be exactly "{status}".\n'
             '"assessment": 1 or 2 short paragraphs in plain words for a family caregiver: how the day looks overall, which finding matters most and why, and what is NOT a concern (for example the other measures that stayed within the usual range). Cite the flag ids. '
             '"changes": one item per flag, each 2 to 4 sentences like a clinical handover note: name the exact measure (for example "systolic blood pressure", never just "a vital sign" or "the activity pattern"), give its recorded value and the time/date, how far it is from the person\'s own usual and in which direction (above or below), and what that could mean for today in plain words, without diagnosing. Each flag has "specifics" (sentences with the recorded numbers): restate at least one of those numbers in your own words. Cite its id (every flag id must be cited at least once). '
             '"actions": 1 to 5 items, most urgent first, built on "proposed_plan": keep each action\'s owner and keep priority NOW only where the plan has NOW (a fall or a critical reading); you may adapt the wording to the recorded values and add one practical detail, but do not drop an action that has an "escalate_if". '
             'Each action says WHAT to do, HOW and WHEN, naming the exact measure or behaviour; "escalate_if" gives the sign or limit and the step to take (quote limits only from "escalation_limits"); include at least one CAREGIVER action. No generic items such as "watch for signs of discomfort". '
             '"guidance": 0 to 2 items, each a 1 to 2 sentence PARAPHRASE of a source that supports the finding, citing its id; [] if no source fits.\n\n'
             'PATIENT CONTEXT:\n{context}')
RETRY_ASK = ('Your previous reply was rejected by the checker for these reasons: {errors}. Write the reply again, fixing exactly these points. No tools are available now; '
             'use only the facts and sources below. Reply with ONE JSON object that follows exactly this JSON Schema (every list item is an object, never a bare string; no other keys): {shape}. '
             'status must be exactly "{status}". Flag ids to cover: {flags}.\n\n'
             'PATIENT CONTEXT:\n{context}\n\nRETRIEVED SOURCES:\n{sources}\n\nLAST 7 DAYS:\n{last7}')
TOOLS = [
    {"name": "get_last_7_days", "description": "The same structured facts for each of the last 7 days (oldest first), compact.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "retrieve_guidance", "description": "Guideline chunks / PubMed abstracts for one finding type, each with a citable id. Only for MONITOR or URGENT days.",
     "parameters": {"type": "object", "properties": {
         "finding_type": {"type": "string", "enum": ["fall", "fall_inactivity", "bp_high", "bp_low", "hr_high", "hr_low", "temperature", "weight_change", "blood_pressure", "vitals_news2", "agitation", "routine_change"]},
         "details": {"type": "object", "properties": {"kind": {"type": "string"}, "post_fall_inactive": {"type": "boolean"}}}},
         "required": ["finding_type"]}},
]
FINDING_TYPES = TOOLS[1]["parameters"]["properties"]["finding_type"]["enum"]
_HIDDEN = {"risk", "drift_score"}      # model scores are never shown to the LLM (and so can never be quoted)


def llm_view(facts: dict) -> dict:
    """The model-independent structured input: facts without model scores or triage internals."""
    def strip(x):
        if isinstance(x, dict):
            return {k: strip(v) for k, v in x.items() if k not in _HIDDEN}
        return [strip(v) for v in x] if isinstance(x, list) else x
    v = strip({k: facts[k] for k in ("patient_id", "day", "status", "vitals", "routine", "falls", "flags", "quality", "has_reading", "has_activity") if k in facts})
    return v


def src_view(sources: list, n: int = 700) -> list:
    return [{"id": x["id"], "title": x["title"], "source": x["source"], "year": x.get("year"), "page": x.get("page"), "for": x.get("finding_type"), "text": x["text"][:n]} for x in sources]


def context_view(facts: dict, sources: list, recent: list) -> dict:
    """What the model (and the validator, as its list of allowed numbers) sees: the facts without model scores, the rule-based starting plan,
    the escalation limits, the prefetched sources and a compact last-7-days strip."""
    from echos.reports.daily import actions as ACT
    v = llm_view(facts)
    v["escalation_limits"] = ACT.thresholds()
    v["proposed_plan"] = [{k: a[k] for k in ("priority", "owner", "text", "escalate_if", "cites")} for a in ACT.build(facts)]
    v["sources"] = src_view(sources)
    v["recent_days"] = recent
    return v


def _json(raw: str):
    t = raw.strip()
    if t.startswith("```"):
        t = t.strip("`").removeprefix("json").strip()
    return json.loads(t)


# ------------------------------------------------------------------ tools
class Tools:
    def __init__(self, eng):
        self.eng = eng

    def get_last_7_days(self, patient_id: str, day: str) -> list:
        return F.get_last_7_days(self.eng, patient_id, day)

    @staticmethod
    def get_guideline(finding_type: str, details: dict | None = None, k: int = 3) -> list:
        """RAG retriever over the local guideline index: chunks with citation ids [KB-n] (empty when RAG is off)."""
        return _safe_retrieve(finding_type, details, k)

    @staticmethod
    def validate_report(output: dict, payload: dict, required_ids=None, valid_ids=None, cite_sections=(), guidance=None) -> dict:
        return validate_report(output, payload, required_ids, valid_ids, cite_sections, guidance)


def _default_pubmed():
    from echos.knowledge.pubmed import PubMedClient
    return PubMedClient()


def _fmt(x):
    return f"{x:g}" if isinstance(x, (int, float)) else str(x)


# ------------------------------------------------------------------ daily
def _spec(e: dict) -> str:
    """' ' + the finding's number-bearing sentences (which vital / routine measure, its value, the usual, the difference), or ''."""
    return (" " + " ".join(e["specifics"])) if e.get("specifics") else ""


def _flag_items(facts: dict):
    """Deterministic wording for each flag -> (changes, monitor) lists."""
    ch, mon = [], []
    for f in facts["flags"]:
        e, i = f["evidence"], [f["id"]]
        t = f["type"]
        if t in ("fall", "fall_inactivity"):
            at = f" at {e['time']}" if e.get("time") else ""
            ch.append({"text": f"A possible fall was detected{at}" + (", followed by little movement." if e.get("post_fall_inactive") else "."), "cites": i})
            mon.append({"text": "Check on them in person and ask how they feel; look for any pain or injury.", "cites": i})
        elif e.get("kind") == "critical":
            ch.append({"text": "A reading in the critical range was recorded: " + F.vitals_detail_text(e.get("values") or {k: e.get(k) for k in ("sys", "dia", "hr")}, e.get("reading_time"),
                                                                                                   e.get("baselines"), bool(e.get("baseline_established"))) + "." + (" Why it fired: " + e["why"] + "." if e.get("why") else "")
                       + _spec(e), "cites": i})
            mon.extend({"text": a, "cites": i} for a in (e.get("action_hints") or ["Contact the care team or doctor today about this reading and repeat it at rest."]))
        elif e.get("kind") == "vitals_alert":
            parts = []
            for k, nm in (("sys", "Systolic blood pressure"), ("dia", "Diastolic blood pressure"), ("hr", "Heart rate")):
                now, base = e.get(k + "_now"), e.get(k + "_baseline")
                if now is not None and base is not None and now != base:
                    parts.append(f"{nm} {_fmt(now)} was {'above' if now > base else 'below'} this person's usual range (usual {_fmt(base)})")
            if e.get("values"):
                txt = "Vitals differed from this person's usual: " + F.vitals_detail_text(e["values"], e.get("reading_time"), e.get("baselines"), bool(e.get("baseline_established"))) + "."
            else:
                txt = ("; ".join(parts) + ".") if parts else F.NOT_STORED + "."
            if e.get("why"):
                txt += " Why it fired: " + e["why"] + "."
            ch.append({"text": txt + _spec(e), "cites": i})
            mon.extend({"text": a, "cites": i} for a in (e.get("action_hints") or ["Repeat the blood pressure and pulse later today, sitting and rested, and write the numbers down."]))
        elif t == "agitation":
            nb = e.get("night_events_baseline")
            extra = f" Night-time movements: {_fmt(e['night_events'])} against usual {_fmt(nb)}." if e.get("night_events") is not None and nb is not None else ""
            ch.append({"text": "The activity pattern differs from usual." + (_spec(e) or extra), "cites": i})
            mon.extend({"text": a, "cites": i} for a in (e.get("action_hints") or ["Watch for restlessness or distress, especially in the evening and at night, and note what seems to trigger it."]))
        elif t == "routine_change":
            feats = ", ".join(FEAT_NAME.get(x, x.replace("_", " ")) for x in e.get("features", [])) or "daily activity"
            ch.append({"text": f"The activity pattern differs from usual ({feats})." + _spec(e), "cites": i})
            mon.extend({"text": a, "cites": i} for a in (e.get("action_hints") or ["Notice how often they get up at night or move between rooms, and whether meals and drinks are taken."]))
    return ch, mon


VNAME = {"sys": "Systolic blood pressure", "dia": "Diastolic blood pressure", "hr": "Heart rate", "temp": "Body temperature", "weight": "Body weight"}
RNAME = {"total_events": "daily room-sensor events", "night_events": "night-time room-sensor events", "night_bathroom": "night-time bathroom visits"}


def daily_situation(facts: dict, alerts: list) -> str:
    """The S of SBAR: one or two lines, deterministic. Names the headline alert when there is one."""
    n = len([f for f in facts["flags"] if f["type"] != "data_gap"])
    if facts["status"] == "NORMAL":
        head = f"{LABEL['NORMAL']} day: nothing differed from this person's own usual pattern on {facts['day']}."
    else:
        head = f"{LABEL[facts['status']]} on {facts['day']}: {n} finding{'' if n == 1 else 's'} differ{'s' if n == 1 else ''} from this person's own usual pattern."
    return head + (" " + alerts[0] if alerts else "")


def daily_background(facts: dict) -> list:
    """The B of SBAR: how to read the tables (what 'usual' means). Per-measure usuals are in the tables themselves."""
    c = load_config()
    return [f"Usual means this person's own average over their previous {c['agent1']['baseline_window_days']} days, never other people's; "
            "a measure counts as outside the usual range when it is much further from that average than this person's own days normally vary (the table shows how far, in SD)."]


def template_assessment(facts: dict) -> list:
    """Deterministic assessment paragraph (the LLM may replace it): overall picture, the lead finding, and what stayed within the usual range."""
    fl = [f for f in facts["flags"] if f["type"] != "data_gap"]
    if not fl:
        return [{"text": "Everything looked within this person's own usual pattern today; no action beyond the routine is needed.", "cites": []}]
    lead = fl[0]
    first = (lead["evidence"].get("specifics") or [None])[0]
    if lead["type"] in ("fall", "fall_inactivity"):
        t = "A possible fall is the most important finding today" + (", followed by little movement" if lead["evidence"].get("post_fall_inactive") else "") + ". Checking on the person comes before anything else."
    elif lead["evidence"].get("kind") == "critical":
        t = "A reading in the critical range is the most important finding today. " + (first or "") + " It should be repeated at rest and the care team told today."
    else:
        t = f"{len(fl)} finding{'' if len(fl) == 1 else 's'} differ{'s' if len(fl) == 1 else ''} from this person's usual today; none reached an emergency limit. " + (first or "")
    within = [VNAME[k].lower() for k, v in (facts.get("vitals") or {}).items() if v.get("value") is not None and v.get("baseline") is not None
              and (v.get("z") is None or abs(v["z"]) < (load_config().get("weekly") or {}).get("band_sd", 2.0))]
    if within and lead["type"] not in ("fall", "fall_inactivity"):
        t += f" The other measures ({', '.join(within[:3])}) stayed within their usual range."
    return [{"text": t.strip(), "cites": [f["id"] for f in fl[:3]]}]


def template_guidance(sources: list, flags: list) -> list:
    """No generated advice: name the best retrieved source for each finding so the care team knows where to read."""
    out, done = [], set()
    for s in sources:
        ft = s.get("finding_type")
        if ft in done:
            continue
        done.add(ft)
        label = GUIDE_LABEL.get(ft, str(ft).replace("_", " "))
        out.append({"text": f"For {label}, the care team can read {DOC.cite_label(s)} (listed under References).", "cites": [s["id"]]})
    return out[:2]


def daily_template_items(facts: dict, sources: list | None = None) -> dict:
    """The whole rule-based report body: assessment, changes (one per flag), the structured action plan and the source pointers."""
    from echos.reports.daily import actions as ACT
    ch, _ = _flag_items(facts)
    if not ch:
        ch = [{"text": "Everything looked normal compared with this person's own usual pattern.", "cites": []}]
    srcs = sources or []
    acts = ACT.build(facts)
    for a in acts:           # attach the best retrieved source of the same finding type to each action
        s = next((x for x in srcs if x.get("finding_type") == a.get("topic")), None)
        if s is not None and s["id"] not in a["cites"]:
            a["cites"] = a["cites"] + [s["id"]]
    return {"assessment": template_assessment(facts), "changes": ch, "actions": acts, "guidance": template_guidance(srcs, facts["flags"])}


def daily_alerts(facts: dict) -> list:
    out = []
    for f in facts["falls"]:
        out.append(f"Possible fall at {f['time'] or 'unknown time'} (likelihood {fmt_prob(f['prob'])}; peak {'not recorded' if f.get('peak_g') is None else str(f['peak_g']) + ' g'}; simulated signal)"
                   + ("; little movement afterwards." if f["post_fall_inactive"] else "."))
    for f in facts["flags"]:
        if f["evidence"].get("kind") == "critical":
            e = f["evidence"]
            out.append("Critical-range vitals reading on " + facts["day"] + ": "
                       + F.vitals_detail_text(e.get("values") or {k: e.get(k) for k in ("sys", "dia", "hr")}, e.get("reading_time"), e.get("baselines"), bool(e.get("baseline_established"))) + ".")
    return out


def daily_data_note(facts: dict):
    """Data note from Agent 0's output (quality) plus the triage DATA_GAP reasons."""
    notes, q = [], facts.get("quality") or {}
    if "quality" in facts and not q.get("checked", False):
        notes.append("The data-quality check did not run today, so today's data was used unchecked.")
    if q.get("sensor_gap"):
        notes.append("The room sensors were silent or unusually quiet today (sensor gap), so the routine could not be checked; "
                     "this is not treated as no movement.")
    elif not facts["has_activity"]:
        notes.append("No room-sensor activity was recorded today, so the daily routine could not be checked.")
    if not facts["has_reading"]:
        notes.append("No blood pressure or heart rate reading was recorded today, so vitals could not be compared with the usual level.")
    dsl = q.get("days_since_last_reading")
    if dsl is not None and dsl > load_config()["triage"]["max_vitals_gap_days"]:
        notes.append(f"No vitals reading for {dsl} days.")
    inv = q.get("invalid_values") or []
    if inv:
        notes.append(f"{len(inv)} implausible measurement(s) ({', '.join(sorted({i['device_type'] for i in inv}))}) were ignored.")
    return " ".join(notes) or None


# ------------------------------------------------------------------ weekly
def _series_stats(vals: list, bases: list):
    v = [x for x in vals if x is not None]
    b = [x for x in bases if x is not None]
    if not v:
        return {"n": 0, "mean": None, "min": None, "max": None, "baseline": None, "delta": None}
    mean = round(sum(v) / len(v), 1)
    base = round(sum(b) / len(b), 1) if b else None
    return {"n": len(v), "mean": mean, "min": round(min(v), 1), "max": round(max(v), 1), "baseline": base,
            "delta": round(mean - base, 1) if base is not None else None}


def weekly_stats(days: list, patient_id: str, end_day: str) -> dict:
    vit = {v: _series_stats([d["vitals"][v]["value"] for d in days], [d["vitals"][v]["baseline"] for d in days])
           for v in F.VITAL_VARS}
    rou = {v: _series_stats([d["routine"][v]["value"] for d in days], [d["routine"][v]["baseline"] for d in days])
           for v in F.ROUTINE_VARS}
    groups: dict = {}
    for d in days:
        for f in d["flags"]:
            if f["type"] == "data_gap":
                continue
            key = f["type"] + (":critical" if f["evidence"].get("kind") == "critical" else "")
            groups.setdefault(key, []).append({"day": d["day"], **f["evidence"]})
    evidence = [{"id": f"E{i + 1}", "type": k.split(":")[0], "kind": "critical" if ":" in k else "alert",
                 "count": len(v), "days": sorted({x["day"] for x in v}), "detail": v}
                for i, (k, v) in enumerate(sorted(groups.items()))]
    n = len(days)
    comp = {"days_covered": n, "days_with_vitals": sum(d["has_reading"] for d in days),
            "days_with_activity": sum(d["has_activity"] for d in days),
            "days_with_temperature": sum(d["vitals"]["temp"]["value"] is not None for d in days),
            "days_with_weight": sum(d["vitals"]["weight"]["value"] is not None for d in days),
            "days_with_data_gap": sum(d["data_gap"] for d in days)}
    status = {k: sum(d["status"] == k for d in days) for k in LABEL}
    falls = [{"day": d["day"], **f} for d in days for f in d["falls"]]
    guides, seen = [], set()
    for e in evidence:  # RAG: guideline chunks for each finding type present this week (deduplicated by chunk id)
        for g in _safe_retrieve(e["type"], {"kind": e["kind"]}):
            if g["id"] not in seen:
                seen.add(g["id"])
                guides.append(g)
    series = {"days": [d["day"] for d in days],
              **{v: [d["vitals"][v]["value"] for d in days] for v in F.VITAL_VARS},
              **{v + "_base": [d["vitals"][v]["baseline"] for d in days] for v in F.VITAL_VARS},
              **{v: [d["routine"][v]["value"] for d in days] for v in F.ROUTINE_VARS},
              **{v + "_base": [d["routine"][v]["baseline"] for d in days] for v in F.ROUTINE_VARS},
              "alert_days": [d["day"] for d in days if d["status"] != "NORMAL"]}
    return {"patient_id": patient_id, "week_ending": end_day, "vitals": vit, "routine": rou, "evidence": evidence,
            "falls": falls, "completeness": comp, "status_counts": status, "guidelines": guides, "series": series}


def weekly_payload(stats: dict) -> dict:
    return {k: v for k, v in stats.items() if k != "series"}


def weekly_template_items(stats: dict) -> dict:
    out = []
    ev_ids = [e["id"] for e in stats["evidence"]]
    c = stats["completeness"]
    for v in ("sys", "dia", "hr", "temp", "weight"):
        s = stats["vitals"][v]
        if s["n"] == 0:
            continue
        base = f" against a personal baseline of {_fmt(s['baseline'])}" if s["baseline"] is not None else " (no baseline yet)"
        out.append({"text": f"{VITAL_NAME[v]} averaged {_fmt(s['mean'])} {UNIT[v]} (range {_fmt(s['min'])} to {_fmt(s['max'])}, "
                            f"{s['n']} days with readings){base}.", "cites": [e["id"] for e in stats["evidence"]
                                                                              if e["type"] in VITALS_TYPES] if v in ("sys", "dia", "hr") else []})
    for e in stats["evidence"]:
        label = {**{t: "Vitals differed from baseline" for t in VITALS_TYPES},
                 "agitation": "Agitation flagged", "routine_change": "Routine drift flagged",
                 "fall": "Possible fall detected", "fall_inactivity": "Possible fall with inactivity detected"}.get(e["type"], e["type"])
        extra = " (critical range)" if e["kind"] == "critical" else ""
        occ = sorted((x for x in e["detail"] if x.get("specifics")), key=lambda x: x["day"])
        if occ:      # which measure, its value, the person's usual and the difference on each occasion (the first sentence of each day's finding)
            shown = "; ".join(f"{x['day']}: {x['specifics'][0].rstrip('.')}" for x in occ[:3]) + (f"; and {len(occ) - 3} more day(s)" if len(occ) > 3 else "")
            out.append({"text": f"{label}{extra} on {e['count']} occasion(s). {shown}.", "cites": [e["id"]]})
        else:
            out.append({"text": f"{label}{extra} on {e['count']} occasion(s): {', '.join(e['days'])}.", "cites": [e["id"]]})
    r = stats["routine"]
    if r["night_events"]["n"]:
        out.append({"text": f"Night-time activity averaged {_fmt(r['night_events']['mean'])} events per night"
                            + (f" against a baseline of {_fmt(r['night_events']['baseline'])}" if r["night_events"]["baseline"] is not None else "")
                            + f"; night-time bathroom visits averaged {_fmt(r['night_bathroom']['mean'])}.",
                    "cites": [i for i in ev_ids if any(e["id"] == i and e["type"] in ("agitation", "routine_change") for e in stats["evidence"])]})
    out.append({"text": f"Data completeness: vitals on {c['days_with_vitals']} of {c['days_covered']} days, "
                        f"room-sensor activity on {c['days_with_activity']} of {c['days_covered']} days.", "cites": []})
    if not stats["evidence"]:
        out.insert(0, {"text": "No alerts were raised this week.", "cites": []})
    return {"highlights": out, "guidance": guidance_template_items(stats)}


GUIDE_LABEL = {"blood_pressure": "blood pressure changes", "vitals_news2": "vital-sign changes", "bp_high": "high blood pressure", "bp_low": "low blood pressure",
               "hr_high": "high heart rate", "hr_low": "low heart rate", "temperature": "temperature change", "weight_change": "weight change", "agitation": "agitation or restlessness",
               "routine_change": "routine changes", "fall": "a possible fall", "fall_inactivity": "a possible fall with inactivity"}


def guidance_template_items(stats: dict) -> list:
    """Template fallback: no generated advice, just the source names of the best retrieved chunk per finding type."""
    out, done = [], set()
    for g in stats["guidelines"]:
        if g["finding_type"] in done:
            continue
        done.add(g["finding_type"])
        out.append({"text": f"For {GUIDE_LABEL.get(g['finding_type'], g['finding_type'])}, see {g['source']}, page {g['page']}; "
                            "for clinician review.", "cites": [g["id"]]})
    return out


def _png64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    import matplotlib.pyplot as plt
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def weekly_charts(series: dict) -> dict:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    x = [pd.Timestamp(d).strftime("%a %d") for d in series["days"]]
    alert = {pd.Timestamp(d).strftime("%a %d") for d in series["alert_days"]}

    def nn(v):
        return [float("nan") if a is None else a for a in v]

    def panel(ax, name, keys, title, unit, colors=("#2563eb", "#d97706")):
        for k, c in zip(keys, colors):
            ax.plot(x, nn(series[k]), "o-", color=c, label=VITAL_NAME.get(k, k.replace("_", " ")))
            if any(b is not None for b in series[k + "_base"]):
                ax.plot(x, nn(series[k + "_base"]), "--", color=c, alpha=.5, label="usual (baseline)" if k == keys[0] else None)
        for i, lab in enumerate(x):
            if lab in alert:
                ax.axvspan(i - .4, i + .4, color="#fecaca", alpha=.5, lw=0)
        ax.set_title(title, fontsize=10, loc="left")
        ax.set_ylabel(unit, fontsize=8)
        ax.tick_params(labelsize=7)
        ax.spines[["top", "right"]].set_visible(False)
        ax.legend(fontsize=7, frameon=False)

    f1, axs = plt.subplots(2, 2, figsize=(9, 5.5), constrained_layout=True)
    panel(axs[0, 0], "bp", ["sys", "dia"], "Blood pressure", "mmHg")
    panel(axs[0, 1], "hr", ["hr"], "Heart rate", "bpm")
    panel(axs[1, 0], "temp", ["temp"], "Body temperature", "°C")
    panel(axs[1, 1], "weight", ["weight"], "Body weight", "kg")
    f2, axr = plt.subplots(1, 3, figsize=(9, 2.8), constrained_layout=True)
    for ax, k, t in zip(axr, ["total_events", "night_events", "night_bathroom"],
                        ["Total activity", "Night-time activity", "Night bathroom visits"]):
        ax.bar(x, [0 if v is None else v for v in series[k]], color="#64748b")
        if any(b is not None for b in series[k + "_base"]):
            ax.plot(x, nn(series[k + "_base"]), "--", color="#d97706", label="usual (baseline)")
            ax.legend(fontsize=7, frameon=False)
        ax.set_title(t, fontsize=10, loc="left")
        ax.tick_params(labelsize=7)
        ax.spines[["top", "right"]].set_visible(False)
    return {"vitals": _png64(f1), "routine": _png64(f2)}


# ------------------------------------------------------------------ generic LLM-with-validation loop
def _write(llm, payload: dict, ask: str, required, valid, cite_sections, template_items: dict, retries: int, ctx: str = "", guidance=None):
    """Return (items, mode, validation, notes). Falls back to template_items when LLM is off or fails.
    A timeout is never retried; other failures get at most `retries` retry. Every fallback is logged with its reason."""
    notes, reason = [], None
    if llm is not None:
        feedback = ""
        for attempt in range(retries + 1):
            try:
                raw = llm.generate(SYSTEM, ask + feedback + "\n\nINPUT JSON:\n" + json.dumps(payload, default=str))
                out = json.loads(raw)
            except TimeoutError as ex:  # includes LLMTimeout: no retry on timeouts
                notes.append(f"attempt {attempt + 1}: timeout")
                reason = f"timeout ({type(ex).__name__})"
                break
            except Exception as ex:  # network / quota / bad JSON -> counts as a failed attempt
                if isinstance(ex, LLMAPIError):  # keep HTTP code + error status, e.g. "429 RESOURCE_EXHAUSTED"
                    notes.append(f"attempt {attempt + 1}: {ex}")
                    reason = f"api_error {ex.code} {ex.status}: {ex.message[:120]}"
                    if ex.code == 429:  # the client already retried once after the suggested delay
                        break
                else:
                    notes.append(f"attempt {attempt + 1}: {type(ex).__name__}")
                    reason = f"error ({type(ex).__name__})"
                feedback = "\n\nYour previous reply was not valid JSON. Reply with JSON only."
                continue
            v = validate_report(out, payload, required, valid, cite_sections, guidance)
            if v["ok"]:
                return out, "llm", v, notes
            notes.append(f"attempt {attempt + 1}: " + "; ".join(v["errors"][:4]))
            reason = "validation_failed"
            feedback = "\n\nYour previous reply was rejected for: " + "; ".join(v["errors"][:6]) + ". Fix these and reply again."
        FALLBACK_LOG.warning("report fallback to template [%s] reason=%s", ctx, reason)
    v = validate_report(template_items, payload, required, valid, cite_sections, guidance)
    if reason:
        v["fallback_reason"] = reason
    return template_items, "template", v, notes


FALLBACK_LOG = logging.getLogger("echos.llm.fallback")
_AGENTS: "weakref.WeakSet" = weakref.WeakSet()


def _setup_fallback_log():
    if not any(isinstance(h, logging.FileHandler) for h in FALLBACK_LOG.handlers):
        path = ROOT / "data" / "llm_fallbacks.log"
        path.parent.mkdir(exist_ok=True)
        h = logging.FileHandler(path)
        h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        FALLBACK_LOG.addHandler(h)
        FALLBACK_LOG.setLevel(logging.INFO)


def drain_all(timeout: float | None = None) -> None:
    """Wait for background LLM report upgrades of every ReportAgent (used at the end of runs / tests)."""
    for a in list(_AGENTS):
        a.drain(timeout)


class ReportAgent:
    """llm: legacy `generate(system, user)` client (weekly report only). budget: LLMBudget for the daily report (cache -> primary -> fallback ->
    template); an llm object that has `.step` is wrapped into a single-model budget with an in-memory cache. pubmed: PubMedClient (default: built
    lazily from config, only when a MONITOR/URGENT day actually retrieves)."""
    def __init__(self, eng, llm=None, root: Path | None = None, budget: LLMBudget | None = None, pubmed=None, retriever=None):
        if budget is None and llm is not None and hasattr(llm, "step"):
            nm = getattr(llm, "model", None)
            budget = LLMBudget([Backend(nm if isinstance(nm, str) else type(llm).__name__, llm)], Cache(":memory:"))
        self.budget = budget
        if llm is not None and not hasattr(llm, "generate"):
            llm = None     # step-only client: no weekly LLM wording
        self.retriever = retriever or GatedRetriever(lambda ft, d=None, k=None: _safe_retrieve(ft, d, k), pubmed=pubmed,
                                                     pubmed_factory=None if pubmed is not None else _default_pubmed)
        self._local_retriever = retriever or GatedRetriever(lambda ft, d=None, k=None: _safe_retrieve(ft, d, k), pubmed=None)     # template-only reports: local index, no outbound call
        self.eng, self.llm, self.tools = eng, llm, Tools(eng)
        cfg = load_config()
        self.retries = (cfg.get("llm") or {}).get("retries", 1)
        self.root = Path(root) if root else ROOT
        self.rc = cfg["reports"]
        self.max_pending = (cfg.get("llm") or {}).get("max_pending", 200)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="report-llm")
        self._futs: list = []
        _AGENTS.add(self)
        if llm is not None or budget is not None:
            _setup_fallback_log()

    # ---- the LLM never sits on the alert path: with defer_llm the template report is written immediately and the
    # LLM version replaces it later from a single background worker (bounded backlog, so a slow LLM cannot grow forever).
    def _defer(self, fn, *args, ctx: str):
        self._futs = [f for f in self._futs if not f.done()]
        if len(self._futs) >= self.max_pending:
            FALLBACK_LOG.warning("report fallback to template [%s] reason=backlog_full", ctx)
            return
        self._futs.append(self._pool.submit(self._safe, fn, *args, ctx))

    @staticmethod
    def _safe(fn, *args):
        *a, ctx = args
        try:
            fn(*a)
        except Exception as ex:  # noqa: BLE001 - a failed upgrade keeps the template report
            FALLBACK_LOG.warning("report fallback to template [%s] reason=upgrade_error (%s)", ctx, type(ex).__name__)

    def drain(self, timeout: float | None = None) -> None:
        wait(list(self._futs), timeout=timeout)

    def _record(self, pid, day, kind, mode, path, validation, provenance=None):
        with self.eng.begin() as c:
            S.upsert(c, S.reports, ["patient_id", "day", "kind"], patient_id=pid, day=day, kind=kind, mode=mode,
                     path=str(path), validation=S.j(validation), created_ts=S.now(), provenance=S.j(provenance) if provenance is not None else None)

    def daily(self, facts: dict, save: bool = True, defer_llm: bool = False, use_llm: bool = True) -> dict:
        if save:
            F.save_day_facts(self.eng, facts)
        if not use_llm:  # NORMAL-day gate: template only, no LLM and no retrieval
            return self._daily(facts, False, "gate: NORMAL day (template only)")
        if self.budget is None:
            return self._daily(facts, False, "no LLM configured")
        if defer_llm:
            out = self._daily(facts, False, "template written first; LLM upgrade pending")
            self._defer(self._daily, facts, True, None, ctx=f"daily {facts['patient_id']} {facts['day']}")
            return out
        return self._daily(facts, True, None)

    # ---- tool loop: one generation attempt on one model
    def _exec_tool(self, call: dict, facts: dict, view: dict, tr: dict) -> dict:
        name, args = call.get("name"), call.get("args") or {}
        if name == "get_last_7_days":      # patient and day are pinned by code: the model cannot read another patient
            days = [pseudonymise(llm_view(d), facts["patient_id"]) for d in self.tools.get_last_7_days(facts["patient_id"], facts["day"])]
            tr["last7"] = days
            return {"days": days}
        if name == "retrieve_guidance":
            ft = args.get("finding_type")
            if ft not in FINDING_TYPES:
                return {"error": f"finding_type must be one of {FINDING_TYPES}"}
            det = {k: v for k, v in (args.get("details") or {}).items() if k in ("kind", "post_fall_inactive")} if isinstance(args.get("details"), dict) else {}
            r = self.retriever.retrieve(facts["status"], ft, det)
            for src in r["sources"]:
                tr["sources"][src["id"]] = src
            tr["retrieval_events"] += r["events"]
            if r["pubmed_failure"]:
                tr["pubmed_failure"] = r["pubmed_failure"]
            return {"sources": [{"id": x["id"], "title": x["title"], "source": x["source"], "text": x["text"][:700]} for x in r["sources"]],
                    "note": "; ".join(r["events"])}
        return {"error": f"unknown tool {name}"}

    @staticmethod
    def _summary(call: dict, res: dict) -> str:
        if "error" in res:
            return f"error: {str(res['error'])[:80]}"
        if call["name"] == "retrieve_guidance":
            return f"{len(res['sources'])} source(s): " + ", ".join(x["id"] for x in res["sources"])
        if call["name"] == "get_last_7_days":
            return f"{len(res['days'])} day(s)"
        return "ok"

    def _generate(self, ctx, facts: dict, view: dict, tr: dict) -> dict:
        """The ONE tool loop: patient context is in the first prompt; up to MAX_TOOL_STEPS tool calls, then a final request with tools switched off.
        Returns the parsed JSON object (or raises ValueError on unparsable output; the caller turns that into a validator failure)."""
        flags = [f["id"] for f in facts["flags"] if f["type"] != "data_gap"]
        ask = DAILY_ASK.format(day=facts["day"], status=facts["status"], flags=", ".join(flags) or "(none)", types=", ".join(FINDING_TYPES), shape=SCHEMA.prompt_shape(),
                               context=json.dumps(view, default=str))
        messages, steps = [{"role": "user", "text": ask}], 0
        while True:
            final = steps >= MAX_TOOL_STEPS
            r = ctx.step(DAILY_SYSTEM, messages, None if final else TOOLS)
            if not r.calls:
                tr["raw"] = r.text
                return _json(r.text)
            if final:
                raise GenerationFailed(f"model still asked for tools after {MAX_TOOL_STEPS} tool steps")
            results = []
            for call in r.calls:
                if steps >= MAX_TOOL_STEPS:
                    res = {"error": "tool step limit reached; give the final answer now"}
                else:
                    steps += 1
                    res = self._exec_tool(call, facts, view, tr)
                    tr["tools"].append({"name": call["name"], "args": call.get("args") or {}, "result_summary": self._summary(call, res)})
                results.append({"name": call["name"], "response": res})
            messages += [{"role": "model", "calls": r.calls, "raw": r.raw}, {"role": "tool", "results": results}]

    def _retry(self, ctx, facts: dict, view: dict, tr: dict, errors: list) -> dict:
        """ONE request, no tools: same facts + the already-retrieved chunks + the validator's errors. Never repeats the tool loop."""
        flags = [f["id"] for f in facts["flags"] if f["type"] != "data_gap"]
        src = [{"id": x["id"], "title": x["title"], "source": x["source"], "text": x["text"][:700]} for x in tr["sources"].values()]
        ask = RETRY_ASK.format(shape=SCHEMA.prompt_shape(), errors="; ".join(errors[:6]), status=facts["status"], flags=", ".join(flags) or "(none)", context=json.dumps(view, default=str),
                               sources=json.dumps(src), last7=json.dumps(tr["last7"], default=str) if tr["last7"] else "(not fetched)")
        r = ctx.step(DAILY_SYSTEM, [{"role": "user", "text": ask}], None)
        tr["raw"] = r.text
        if r.calls:
            raise ValueError("model asked for a tool in the retry")
        return _json(r.text)

    @staticmethod
    def _check(out, facts: dict, view: dict, tr: dict) -> dict:
        """Strict schema parse first (SCHEMA.DailyReport, the same model the prompt and Gemini's response schema use), then the content validator."""
        model, serrs = SCHEMA.parse(out)
        if model is None:
            return {"ok": False, "errors": serrs, "codes": ["schema_mismatch"], "findings": [{"rule": "schema_mismatch", "detail": e} for e in serrs]}
        out = model.model_dump()
        required = [f["id"] for f in facts["flags"] if f["type"] != "data_gap"]
        sources = tr["sources"]
        guidance = {"section": "guidance", "ids": list(sources), "texts": {i: s["text"] for i, s in sources.items()}}
        v = validate_report(out, {"facts": view, "last_7_days": tr["last7"], "sources": [{k: x.get(k) for k in ("id", "title", "source", "year", "page")} for x in sources.values()]},
                            required, [f["id"] for f in facts["flags"]] + list(sources), ["changes"] if required else [], guidance, expected_status=facts["status"], flags=facts["flags"])
        errs = []
        want_now = any(a.get("priority") == "NOW" for a in (view.get("proposed_plan") or []))
        has_now = any(a["priority"] == "NOW" for a in out["actions"])
        if has_now and not want_now:
            errs.append("priority NOW is only for a fall or a critical reading")
        if want_now and not has_now:
            errs.append("priority NOW action is missing for a fall or critical reading")
        if not any(a["owner"] == "CAREGIVER" for a in out["actions"]):
            errs.append("priority: at least one CAREGIVER action is needed")
        if errs:
            e = sorted(set(v["errors"] + errs))
            v = {**v, "ok": False, "errors": e, "codes": sorted(set(v["codes"]) | {"priority_invalid"}),
                 "findings": v["findings"] + [{"rule": "priority_invalid", "detail": x} for x in errs]}
        return v

    def _attempt(self, facts: dict, view: dict, pre: dict | None = None):
        """-> function(ctx) for LLMBudget.run: tool loop + validate; on a validator failure ONE retry request (no tools); then GenerationFailed (template).
        Unparsable JSON counts as a validator failure ('json_invalid'), so it also gets the single retry rather than a repeat of the tool loop."""
        def attempt(ctx) -> dict:
            pre_ = pre or {}
            tr = {"tools": [], "sources": {x["id"]: x for x in pre_.get("sources", [])}, "last7": view.get("recent_days") or [], "retrieval_events": list(pre_.get("events", [])),
                  "pubmed_failure": pre_.get("pubmed_failure"), "raw": None}
            raws, retried = [], False
            try:
                out = self._generate(ctx, facts, view, tr)
                v = self._check(out, facts, view, tr)
            except ValueError as ex:      # json.JSONDecodeError
                v = {"ok": False, "errors": [f"reply was not valid JSON ({type(ex).__name__})"], "codes": ["json_invalid"], "findings": [{"rule": "json_invalid", "detail": "reply was not valid JSON"}]}
            if not v["ok"]:
                raws.append({"raw": tr["raw"], "validator": v})
                retried = True
                try:
                    out = self._retry(ctx, facts, view, tr, v["errors"])
                    v = self._check(out, facts, view, tr)
                except ValueError as ex:
                    v = {"ok": False, "errors": [f"reply was not valid JSON ({type(ex).__name__})"], "codes": ["json_invalid"], "findings": [{"rule": "json_invalid", "detail": "reply was not valid JSON"}]}
            if v["ok"]:
                return {"items": {"status": out.get("status"), **{k: out.get(k, []) for k in ("assessment", "changes", "actions", "guidance")}}, "tools": tr["tools"], "sources": tr["sources"],
                        "last7": tr["last7"], "retrieval_events": tr["retrieval_events"], "pubmed_failure": tr["pubmed_failure"], "validation": v, "retried": retried,
                        "first_attempt_errors": raws[0]["validator"]["codes"] if raws else []}
            raws.append({"raw": tr["raw"], "validator": v})
            raise GenerationFailed("validation_failed", {**v, "attempts": raws})
        return attempt

    def _prefetch(self, facts: dict, live: bool) -> dict:
        """Retrieval by CODE (not by the model) for MONITOR / URGENT days: one call per distinct finding type, then only the sources that match the finding
        (echos/reports/evidence.pick_sources: local guideline chunks above the score cut-off, at most one PubMed article). `live` = an LLM budget exists, so
        the PubMed client may be used; a template-only agent reads the local index only (no outbound call). NORMAL days make no call at all."""
        out = {"sources": [], "events": [], "pubmed_failure": None}
        if facts["status"] not in GATED_STATUSES:
            return out
        r, c, seen, done = (self.retriever if live else self._local_retriever), EV.cfg(), set(), set()
        for f in facts["flags"]:
            ft = f["type"]
            topic = "fall" if ft in ("fall", "fall_inactivity") else ft
            if ft == "data_gap" or topic in done:
                continue
            done.add(topic)
            e = f["evidence"]
            det = {"post_fall_inactive": bool(e.get("post_fall_inactive"))} if topic == "fall" else {"kind": e["kind"]} if e.get("kind") else {}
            res = r.retrieve(facts["status"], ft, det)
            out["events"] += [f"{topic}: {x}" for x in res["events"]]
            out["pubmed_failure"] = res["pubmed_failure"] or out["pubmed_failure"]
            key = "falls" if topic == "fall" else "routine" if ft in ("agitation", "routine_change") else ft
            picked = EV.pick_sources(key, res["sources"], c) if key in EV.FINDINGS else res["sources"][:2]
            for s in picked:
                if s["id"] not in seen:
                    seen.add(s["id"])
                    out["sources"].append({**s, "finding_type": topic})
        return out

    def _daily(self, facts: dict, use_ai: bool, why_template) -> dict:
        pid = facts["patient_id"]
        live = bool(use_ai and self.budget is not None)
        pre = self._prefetch(facts, live)
        recent = [{"day": d["day"], "status": d["status"], "flags": [f["type"] for f in d["flags"] if f["type"] != "data_gap"]}
                  for d in self.tools.get_last_7_days(pid, facts["day"])]
        view = context_view(facts, pre["sources"], recent)          # facts + proposed plan + limits + sources: the model's input and the validator's list of allowed numbers
        aview = pseudonymise(view, pid)          # what the LLM sees
        required = [f["id"] for f in facts["flags"] if f["type"] != "data_gap"]
        items = daily_template_items(facts, pre["sources"])
        meta = [{k: x.get(k) for k in ("id", "title", "source", "year", "page")} for x in pre["sources"]]
        v = validate_report(items, {"facts": view, "sources": meta}, required, [f["id"] for f in facts["flags"]] + [x["id"] for x in pre["sources"]],
                            ["changes"] if required else [], {"section": "guidance", "ids": [x["id"] for x in pre["sources"]], "texts": {x["id"]: x["text"] for x in pre["sources"]}})
        mode, sources, events = "template", {x["id"]: x for x in pre["sources"]}, []
        prov = {"model": "template", "mode": "template", "cache_hit": False, "tools": [], "sources_retrieved": [], "sources_cited": [], "validator": v,
                "fallback_reason": why_template, "requests": 0, "retried": False, "validator_codes": [], "tokens": {"prompt": 0, "output": 0}, "models_tried": [], "events": [],
                "retrieval_events": pre["events"], "pubmed_failure": pre["pubmed_failure"]}
        if live:
            key = make_key(PROMPT_VERSION, view)
            revalidate = lambda c: self._check(c["items"], facts, aview, {"sources": c["sources"], "last7": c["last7"]})["ok"]  # noqa: E731
            res = self.budget.run(self._attempt(facts, aview, pre), key=key, revalidate=revalidate, prompt_version=PROMPT_VERSION)
            prov.update(requests=res.requests, tokens={"prompt": res.prompt_tokens, "output": res.output_tokens}, models_tried=res.models_tried, events=res.events)
            if res.ok:
                val = res.value
                items, sources, mode = val["items"], val["sources"], "llm"
                v = self._check(items, facts, aview, {"sources": sources, "last7": val["last7"]})
                items = pseudonymise_back(items, pid)       # alias -> patient id, in code only
                prov.update(model=res.model, mode="llm", cache_hit=res.cache_hit, tools=val["tools"], validator=v, fallback_reason=None,
                            retrieval_events=val["retrieval_events"], pubmed_failure=val.get("pubmed_failure"), retried=val.get("retried", False),
                            first_attempt_errors=val.get("first_attempt_errors", []))
            else:
                prov["fallback_reason"] = res.fallback_reason
                if res.validation:
                    prov["validator"] = {k: res.validation[k] for k in ("ok", "errors", "codes", "findings") if k in res.validation}
                    prov["validator_codes"] = res.validation.get("codes", [])
                    prov["retried"] = True
        elif use_ai:
            prov["fallback_reason"] = "no LLM configured"
        prov["sources_retrieved"] = [{k: s.get(k) for k in ("id", "origin", "title", "source", "publisher", "year", "page", "journal", "url", "excerpt")} for s in sources.values()]
        cited = [c for sec in ("assessment", "changes", "actions", "guidance") for it in items.get(sec, []) for c in it.get("cites", []) if c in sources]
        refs = [sources[c] for c in dict.fromkeys(cited)]
        prov["sources_cited"] = [r["id"] for r in refs]
        case = None
        try:        # the Case Investigator's file (headline, evidence level, next checks) goes into the report as written by code, not by the LLM
            from echos.team import bus
            from echos.team.investigator import load_case_file
            case = load_case_file(self.eng, pid, facts["day"])
            if case and case.get("status") == "NORMAL":
                case = None                      # a near-miss WATCH file is not part of the report
            bus.done(self.eng, [m["id"] for m in bus.poll(self.eng, "report", ["case_file_ready"]) if m["patient_id"] == pid and m["day"] == facts["day"]])
        except Exception:  # noqa: BLE001 - the report never depends on it
            case = None
        alerts = daily_alerts(facts)
        doc = DOC.build_doc(facts, alerts=alerts, situation=daily_situation(facts, alerts), background=daily_background(facts), items=items, sources=refs,
                            case=case, mode=mode, data_note=daily_data_note(facts), trend_days=[{"day": d["day"], "status": d["status"]} for d in recent],
                            model=prov["model"] if mode == "llm" else None)
        text = DOC.render_markdown(doc)
        path = self.root / self.rc["daily_dir"] / f"{facts['patient_id']}_{facts['day']}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.with_suffix(".json").write_text(json.dumps(doc, indent=1, default=str))
        if prov["fallback_reason"]:
            v = {**v, "fallback_reason": prov["fallback_reason"]}
        if prov["fallback_reason"] and use_ai:
            FALLBACK_LOG.warning("report fallback to template [daily %s %s] reason=%s", facts["patient_id"], facts["day"], prov["fallback_reason"])
        v["notes"] = [e["detail"] for e in prov["events"] if e["event"] in ("fallback", "cooldown_start", "generation_failed")]
        self._record(facts["patient_id"], facts["day"], "daily", mode, path.relative_to(self.root), v, prov)
        return {"mode": mode, "path": path, "text": text, "doc": doc, "validation": v, "provenance": prov}

    def weekly(self, patient_id: str, day: str, defer_llm: bool = False) -> dict:
        if defer_llm and self.llm is not None:
            out = self._weekly(patient_id, day, None)
            self._defer(self._weekly, patient_id, day, self.llm, ctx=f"weekly {patient_id} {day}")
            return out
        return self._weekly(patient_id, day, self.llm)

    def _weekly(self, patient_id: str, day: str, llm) -> dict:
        days = self.tools.get_last_7_days(patient_id, day)
        stats = weekly_stats(days, patient_id, day)
        payload = weekly_payload(stats)
        required = [e["id"] for e in stats["evidence"]]
        kb = [g["id"] for g in stats["guidelines"]]
        valid = required + kb
        guidance = {"section": "guidance", "ids": kb, "texts": {g["id"]: g["text"] for g in stats["guidelines"]}}
        items, mode, v, notes = _write(llm, payload, WEEKLY_ASK, required, valid, [],
                                       weekly_template_items(stats), self.retries, ctx=f"weekly {patient_id} {day}", guidance=guidance)
        cited = {c for it in items.get("guidance", []) for c in it.get("cites", [])}
        refs = [g for g in stats["guidelines"] if g["id"] in cited]
        html = HTML_TPL.get_template("weekly.html.j2").render(
            s=stats, items=items.get("highlights", []), guidance=items.get("guidance", []), refs=refs, charts=weekly_charts(stats["series"]) if days else {},
            vname=VITAL_NAME, unit=UNIT, mode=mode, label=LABEL, week_start=days[0]["day"] if days else day)
        path = self.root / self.rc["weekly_dir"] / f"{patient_id}_{day}.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html)
        v["notes"] = notes
        self._record(patient_id, day, "weekly", mode, path.relative_to(self.root), v)
        return {"mode": mode, "path": path, "validation": v}
