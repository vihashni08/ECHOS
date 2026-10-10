"""Agent 4: Report agent. Daily caregiver report + weekly clinician report.

Pipeline (tools: get_last_7_days, get_guideline, validate_report): structured facts -> [LLM wording] -> non-LLM
validator -> retry once with the validator's errors -> Jinja2 template fallback. The LLM only phrases items from the
input JSON; status, alerts, data notes and all tables/charts are produced by code. The LLM never decides emergencies.
"""
from __future__ import annotations

import base64
import io
import json
import time
from pathlib import Path

import jinja2
import pandas as pd
from sqlalchemy import select

from echos.config import ROOT, load_config
from echos.orchestrator import state as S
from echos.reports import facts as F
from echos.reports.knowledge import get_guideline
from echos.reports.validator import validate_report

TPL = jinja2.Environment(loader=jinja2.FileSystemLoader(str(Path(__file__).parent / "templates")), autoescape=False,
                         trim_blocks=True, lstrip_blocks=True)
HTML_TPL = jinja2.Environment(loader=jinja2.FileSystemLoader(str(Path(__file__).parent / "templates")), autoescape=True,
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
WEEKLY_ASK = ('Write for a clinician. Return {"highlights":[{"text":str,"cites":[evidence or guideline ids]}]} with 3 to 6 '
              'factual highlights of the week (vitals vs baseline, alerts, routine, data completeness). Each highlight '
              'must cite at least one evidence id (E..), and every evidence id must be cited at least once. Guideline '
              'ids (G..) may only be cited as "for clinician review" context.')


# ------------------------------------------------------------------ tools
class Tools:
    def __init__(self, eng):
        self.eng = eng

    def get_last_7_days(self, patient_id: str, day: str) -> list:
        return F.get_last_7_days(self.eng, patient_id, day)

    @staticmethod
    def get_guideline(finding_type: str) -> list:
        return get_guideline(finding_type)

    @staticmethod
    def validate_report(output: dict, payload: dict, required_ids=None, valid_ids=None, cite_sections=()) -> dict:
        return validate_report(output, payload, required_ids, valid_ids, cite_sections)


def _fmt(x):
    return f"{x:g}" if isinstance(x, (int, float)) else str(x)


# ------------------------------------------------------------------ daily
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
            parts = [f"{k} {_fmt(e[k])}" for k in ("sys", "dia", "hr") if e.get(k) is not None]
            ch.append({"text": "A reading in the critical range was recorded (" + ", ".join(parts) + ").", "cites": i})
            mon.append({"text": "Contact the care team or doctor today about this reading and repeat it at rest.", "cites": i})
        elif e.get("kind") == "vitals_alert":
            parts = [f"{k} {_fmt(e[k + '_now'])} against usual {_fmt(e[k + '_baseline'])}" for k in ("sys", "dia", "hr")
                     if e.get(k + "_now") is not None and e.get(k + "_baseline") is not None]
            ch.append({"text": "Blood pressure or heart rate was different from this person's usual: " + "; ".join(parts) + "." if parts
                       else "Blood pressure or heart rate was different from this person's usual.", "cites": i})
            mon.append({"text": "Repeat the blood pressure and pulse later today, sitting and rested, and write the numbers down.", "cites": i})
        elif t == "agitation":
            nb = e.get("night_events_baseline")
            extra = f" Night-time movements: {_fmt(e['night_events'])} against usual {_fmt(nb)}." if e.get("night_events") is not None and nb is not None else ""
            ch.append({"text": "A more restless pattern than usual was noticed." + extra, "cites": i})
            mon.append({"text": "Watch for restlessness or distress, especially in the evening and at night, and note what seems to trigger it.", "cites": i})
        elif t == "routine_change":
            feats = ", ".join(FEAT_NAME.get(x, x.replace("_", " ")) for x in e.get("features", [])) or "daily activity"
            ch.append({"text": f"The daily routine has shifted from the usual pattern ({feats}).", "cites": i})
            mon.append({"text": "Notice how often they get up at night or move between rooms, and whether meals and drinks are taken.", "cites": i})
    return ch, mon


FILLERS = ["Make sure meals and drinks are taken as usual.", "Keep walkways and the route to the bathroom clear and well lit.",
           "Tell the care team if anything seems out of the ordinary."]


def daily_payload(facts: dict) -> dict:
    return {"patient_id": facts["patient_id"], "day": facts["day"], "status": LABEL[facts["status"]], "facts": facts}


def daily_template_items(facts: dict) -> dict:
    ch, mon = _flag_items(facts)
    if not ch:
        ch = [{"text": "Everything looked normal compared with this person's own usual pattern.", "cites": []}]
    seen, out = set(), []
    for m in mon:
        if m["text"] not in seen:
            seen.add(m["text"])
            out.append(m)
    for f in FILLERS:
        if len(out) >= 2:
            break
        out.append({"text": f, "cites": []})
    return {"changes": ch, "monitor": out[:3]}


def daily_alerts(facts: dict) -> list:
    out = []
    for f in facts["falls"]:
        out.append(f"Possible fall at {f['time'] or 'unknown time'} (likelihood {f['prob']:.2f})"
                   + ("; little movement afterwards." if f["post_fall_inactive"] else "."))
    for f in facts["flags"]:
        if f["evidence"].get("kind") == "critical":
            e = f["evidence"]
            out.append("Critical-range vitals reading on " + facts["day"] + " (exact time of reading not recorded): "
                       + ", ".join(f"{k} {_fmt(e[k])}" for k in ("sys", "dia", "hr") if e.get(k) is not None) + ".")
    return out


def daily_data_note(facts: dict):
    notes = []
    if not facts["has_reading"]:
        notes.append("No blood pressure or heart rate reading was recorded today, so vitals could not be compared with the usual level.")
    if not facts["has_activity"]:
        notes.append("No room-sensor activity was recorded today, so the daily routine could not be checked.")
    for r in facts["triage_reasons"]:
        if str(r).startswith("data_gap:") and "no vitals reading for" in r:
            notes.append(str(r)[len("data_gap:"):].strip().capitalize() + ".")
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
    types = sorted({e["type"] for e in evidence} | ({"data_gap"} if comp["days_with_data_gap"] else set())
                   | ({"weight"} if vit["weight"]["n"] else set()))
    guides = [g for t in types for g in get_guideline(t)]
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
                                                                              if e["type"] in ("blood_pressure", "vitals_news2")] if v in ("sys", "dia", "hr") else []})
    for e in stats["evidence"]:
        label = {"blood_pressure": "Vitals differed from baseline", "vitals_news2": "Vitals differed from baseline",
                 "agitation": "Agitation flagged", "routine_change": "Routine drift flagged",
                 "fall": "Possible fall detected", "fall_inactivity": "Possible fall with inactivity detected"}.get(e["type"], e["type"])
        extra = " (critical range)" if e["kind"] == "critical" else ""
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
    return {"highlights": out}


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
def _write(llm, payload: dict, ask: str, required, valid, cite_sections, template_items: dict, retries: int):
    """Return (items, mode, validation, notes). Falls back to template_items when LLM is off or fails validation twice."""
    notes = []
    if llm is not None:
        feedback = ""
        for attempt in range(retries + 1):
            try:
                raw = llm.generate(SYSTEM, ask + feedback + "\n\nINPUT JSON:\n" + json.dumps(payload, default=str))
                out = json.loads(raw)
            except Exception as ex:  # network / quota / bad JSON -> counts as a failed attempt
                notes.append(f"attempt {attempt + 1}: {type(ex).__name__}")
                feedback = "\n\nYour previous reply was not valid JSON. Reply with JSON only."
                continue
            v = validate_report(out, payload, required, valid, cite_sections)
            if v["ok"]:
                return out, "llm", v, notes
            notes.append(f"attempt {attempt + 1}: " + "; ".join(v["errors"][:4]))
            feedback = "\n\nYour previous reply was rejected for: " + "; ".join(v["errors"][:6]) + ". Fix these and reply again."
    v = validate_report(template_items, payload, required, valid, cite_sections)
    return template_items, "template", v, notes


class ReportAgent:
    def __init__(self, eng, llm=None, root: Path | None = None):
        self.eng, self.llm, self.tools = eng, llm, Tools(eng)
        cfg = load_config()
        self.retries = (cfg.get("llm") or {}).get("retries", 1)
        self.root = Path(root) if root else ROOT
        self.rc = cfg["reports"]

    def _record(self, pid, day, kind, mode, path, validation):
        with self.eng.begin() as c:
            S.upsert(c, S.reports, ["patient_id", "day", "kind"], patient_id=pid, day=day, kind=kind, mode=mode,
                     path=str(path), validation=S.j(validation), created_ts=S.now())

    def daily(self, facts: dict, save: bool = True) -> dict:
        if save:
            F.save_day_facts(self.eng, facts)
        payload = daily_payload(facts)
        required = [f["id"] for f in facts["flags"] if f["type"] != "data_gap"]
        valid = [f["id"] for f in facts["flags"]]
        items, mode, v, notes = _write(self.llm, payload, DAILY_ASK, required, valid, ["changes"] if required else [],
                                       daily_template_items(facts), self.retries)
        text = TPL.get_template("daily.md.j2").render(
            pid=facts["patient_id"], day=facts["day"], label=LABEL[facts["status"]], alerts=daily_alerts(facts),
            changes=items.get("changes", []), monitor=items.get("monitor", [])[:3], data_note=daily_data_note(facts), mode=mode)
        path = self.root / self.rc["daily_dir"] / f"{facts['patient_id']}_{facts['day']}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        v["notes"] = notes
        self._record(facts["patient_id"], facts["day"], "daily", mode, path.relative_to(self.root), v)
        return {"mode": mode, "path": path, "text": text, "validation": v}

    def weekly(self, patient_id: str, day: str) -> dict:
        days = self.tools.get_last_7_days(patient_id, day)
        stats = weekly_stats(days, patient_id, day)
        payload = weekly_payload(stats)
        required = [e["id"] for e in stats["evidence"]]
        valid = required + [g["id"] for g in stats["guidelines"]]
        items, mode, v, notes = _write(self.llm, payload, WEEKLY_ASK, required, valid, [],
                                       weekly_template_items(stats), self.retries)
        html = HTML_TPL.get_template("weekly.html.j2").render(
            s=stats, items=items.get("highlights", []), charts=weekly_charts(stats["series"]) if days else {},
            vname=VITAL_NAME, unit=UNIT, mode=mode, label=LABEL, week_start=days[0]["day"] if days else day)
        path = self.root / self.rc["weekly_dir"] / f"{patient_id}_{day}.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html)
        v["notes"] = notes
        self._record(patient_id, day, "weekly", mode, path.relative_to(self.root), v)
        return {"mode": mode, "path": path, "validation": v}
