"""Structured per-day facts for the Report agent. Everything the LLM or template may say comes from this JSON.

Baselines use only PAST days of the same patient (strictly before `day`), so no leakage into the report.
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Optional

import pandas as pd
from sqlalchemy import select

from echos.config import load_config
from echos.orchestrator import state as S
from echos.reports.daily import specifics as SP
from echos.reports.daily import vitals_why as VW

VITAL_VARS = ["sys", "dia", "hr", "temp", "weight"]
_PDY: dict = {}


def patient_days() -> pd.DataFrame:
    if "df" not in _PDY:
        from echos.data.patient_day import load_patient_day
        _PDY["df"] = load_patient_day()
    return _PDY["df"]


def _r(x, nd=1):
    return None if x is None or pd.isna(x) else round(float(x), nd)


def _past_baseline(pdf: pd.DataFrame, var: str, day: pd.Timestamp, window: int, min_n: int, active_only=False):
    past = pdf[pdf.day < day]
    if active_only:
        past = past[past.has_activity]
    s = past[var].dropna().tail(window)
    if len(s) < min_n:
        return None, None
    return float(s.mean()), float(s.std(ddof=0))


def _round(x):
    return None if x is None else round(x, 1)


VITAL_UNITS = {"sys": "mmHg", "dia": "mmHg", "hr": "bpm", "temp": "C", "weight": "kg"}
_DEV = {"Systolic blood pressure": "sys", "Diastolic blood pressure": "dia", "Heart rate": "hr", "Body Temperature": "temp", "Body weight": "weight"}
NOT_STORED = "Vitals differ from usual; values not stored for this day"


def reading_info(patient_id: str, day: str) -> dict:
    """Raw readings of one day: {"time": 'YYYY-MM-DD HH:MM' of the latest blood-pressure (else any) reading, "values": {var: value of the latest reading}}.
    Best effort: any failure gives {} (the flag then simply carries no values)."""
    try:
        from echos.alerts.payload import _physiology
        g = _physiology(patient_id)
        if g is None:
            return {}
        d = pd.Timestamp(day).normalize()
        g = g[(g.date >= d) & (g.date < d + timedelta(days=1)) & g.device_type.isin(_DEV)].sort_values("date")
        if g.empty:
            return {}
        vals = {_DEV[k]: round(float(x.value.iloc[-1]), 1) for k, x in g.groupby("device_type")}
        bp = g[g.device_type.isin(["Systolic blood pressure", "Diastolic blood pressure"])]
        t = (bp if len(bp) else g).date.iloc[-1]
        return {"time": t.strftime("%Y-%m-%d %H:%M"), "values": vals}
    except Exception:  # noqa: BLE001
        return {}


def _g(x):
    return f"{x:g}"


def vitals_detail_text(values: Optional[dict], time: Optional[str] = None, baselines: Optional[dict] = None, established: bool = False) -> str:
    """One line with the vitals behind a vitals flag, e.g. 'Systolic 174 / diastolic 99 mmHg, heart rate 82 bpm at 2019-04-04 09:12; baseline not yet established'.
    No values -> 'Vitals differ from usual; values not stored for this day'. Never contains a model score."""
    v = {k: x for k, x in (values or {}).items() if x is not None}
    if not v:
        return NOT_STORED
    parts = []
    if "sys" in v and "dia" in v:
        parts.append(f"Systolic {_g(v['sys'])} / diastolic {_g(v['dia'])} mmHg")
    else:
        parts += [f"{n} {_g(v[k])} mmHg" for k, n in (("sys", "Systolic"), ("dia", "Diastolic")) if k in v]
    parts += [f"{n} {_g(v[k])} {VITAL_UNITS[k]}" for k, n in (("hr", "heart rate"), ("temp", "temperature"), ("weight", "weight")) if k in v]
    if parts and not parts[0].startswith(("Systolic", "Diastolic")):
        parts[0] = parts[0][0].upper() + parts[0][1:]
    s = ", ".join(parts) + (f" at {time}" if time else "")
    b = {k: x for k, x in (baselines or {}).items() if x is not None and k in v}
    if established and b:
        bp = f"systolic {_g(b['sys'])} / diastolic {_g(b['dia'])} mmHg" if "sys" in b and "dia" in b else None
        rest = [f"{n} {_g(b[k])} {VITAL_UNITS[k]}" for k, n in (("hr", "heart rate"), ("temp", "temperature"), ("weight", "weight")) if k in b]
        return s + "; usual: " + ", ".join(([bp] if bp else []) + rest)
    return s + "; baseline not yet established"


def build_day_facts(eng, patient_id: str, day: str, decision: dict, results: dict, pdy: Optional[pd.DataFrame] = None) -> dict:
    cfg = load_config()
    t, win = cfg["triage"], cfg["agent1"]["baseline_window_days"]
    d = pd.Timestamp(day).normalize()
    pdy = patient_days() if pdy is None else pdy
    pdf = pdy[pdy.patient_id == patient_id].sort_values("day")
    row = pdf[pdf.day == d]
    row = row.iloc[0] if len(row) else None

    vitals = {}
    for v in VITAL_VARS:
        cur = None if row is None else _r(row[v])
        b, sd = _past_baseline(pdf, v, d, win, 3)
        vitals[v] = {"value": cur, "baseline": _round(b),
                     "z": _r((cur - b) / sd) if cur is not None and b is not None and sd and sd > 0 else None}
    has_activity = bool(row is not None and row["has_activity"])

    health = results.get("health") or {}
    vit = health.get("vitals") or {}
    flags, n = [], 0

    def add(ftype, **ev):
        nonlocal n
        n += 1
        flags.append({"id": f"F{n}", "type": ftype, "evidence": ev})

    # ---- falls (all fall-agent events of the day at or above the MONITOR threshold)
    falls = []
    with eng.begin() as c:
        rows = c.execute(select(S.agent_output.c.result).where((S.agent_output.c.agent_id == "fall") &
                                                                (S.agent_output.c.patient_id == patient_id) &
                                                                (S.agent_output.c.day == day))).fetchall()
    for (res,) in rows:
        r = json.loads(res)
        if r["score"] >= t["fall_prob"]:
            ts = str(r["evidence"].get("ts", ""))
            pk = r["evidence"].get("peak_svm_g")
            falls.append({"time": ts[11:16] if len(ts) >= 16 else None, "prob": round(r["score"], 2), "peak_g": None if pk is None else round(float(pk), 1),
                          "post_fall_inactive": bool(r["evidence"].get("post_fall_inactive"))})
    falls.sort(key=lambda f: f["time"] or "")
    for f in falls:
        add("fall_inactivity" if f["post_fall_inactive"] else "fall", **f)

    # ---- vitals
    if vit.get("current_vs_baseline"):
        cv = vit["current_vs_baseline"]
        ri = reading_info(patient_id, day)
        bases = {k: _r(vitals[k]["baseline"]) for k in VITAL_VARS}
        bases.update({k: _r(v["baseline"]) for k, v in cv.items() if v.get("baseline") is not None})
        detail = {"values": ri.get("values") or {k: x for k, x in ((k, _r(v["current"])) for k, v in cv.items()) if x is not None},
                  "reading_time": ri.get("time"), "baselines": bases, "baseline_established": any(bases.get(k) is not None for k in ("sys", "dia"))}
        cur = {k: _r(v["current"]) for k, v in cv.items()}
        crit = t["critical_vitals"]
        s_, di_, h_ = cur.get("sys"), cur.get("dia"), cur.get("hr")
        zs = {k: v.get("z") for k, v in cv.items()}
        vals_now = {k: x for k, x in detail["values"].items() if x is not None}
        if (s_ is not None and (s_ >= crit["sys_high"] or s_ < crit["sys_low"])) or (di_ is not None and di_ >= crit["dia_high"]) \
                or (h_ is not None and (h_ >= crit["hr_high"] or h_ < crit["hr_low"])):
            kc = VW.classify({k: x for k, x in {"sys": s_, "dia": di_, "hr": h_}.items() if x is not None}, bases, zs=zs, critical=True)
            add(kc["types"][0], kind="critical", sys=s_, dia=di_, hr=h_, why=kc["why"], **detail,
                specifics=SP.vital_sentences(vals_now, bases, zs), action_hints=SP.vital_actions(kc["types"], vals_now, critical=True))
        if vit["probability"] >= t["vitals_prob"] and not health.get("degraded"):
            ka = VW.classify(vals_now, bases, zs=zs)
            add(ka["types"][0], kind="vitals_alert", risk=round(vit["probability"], 2),
                **{f"{k}_now": _r(v["current"]) for k, v in cv.items()},
                **{f"{k}_baseline": _r(v["baseline"]) for k, v in cv.items()}, why=ka["why"], **detail,
                specifics=SP.vital_sentences(vals_now, bases, zs), action_hints=SP.vital_actions(ka["types"], vals_now))
    # ---- data gaps
    gaps = [r for r in decision.get("reasons", []) if str(r).startswith("DATA_GAP:")]
    for g in gaps:
        add("data_gap", note=str(g)[len("DATA_GAP:"):].strip())
    qe = (results.get("quality") or {}).get("evidence", {})
    quality = {"checked": bool(qe) and qe.get("quality_checked", True), "sensor_gap": bool(qe.get("sensor_gap")),
               "sensor_gap_reason": qe.get("sensor_gap_reason"), "days_since_last_reading": qe.get("days_since_last_reading"),
               "invalid_values": qe.get("invalid_values", []), "completeness": qe.get("completeness")}

    return {"patient_id": patient_id, "day": day, "status": decision.get("status", "NORMAL"), "data_gap": bool(decision.get("data_gap")),
            "triage_reasons": list(decision.get("reasons", [])),
            "has_reading": bool(row is not None and row["has_reading"]), "has_activity": has_activity,
            "vitals": vitals, "falls": falls, "flags": flags, "quality": quality}


def save_day_facts(eng, facts: dict) -> None:
    with eng.begin() as c:
        S.upsert(c, S.daily_facts, ["patient_id", "day"], patient_id=facts["patient_id"], day=facts["day"], facts=S.j(facts))


def get_last_7_days(eng, patient_id: str, day: str) -> list:
    """Tool: the patient's stored daily facts for the 7 days ending at `day` (oldest first; missing days omitted)."""
    d = pd.Timestamp(day)
    lo, hi = (d - timedelta(days=6)).strftime("%Y-%m-%d"), d.strftime("%Y-%m-%d")
    with eng.begin() as c:
        rows = c.execute(select(S.daily_facts.c.facts).where((S.daily_facts.c.patient_id == patient_id) &
                                                              (S.daily_facts.c.day >= lo) & (S.daily_facts.c.day <= hi))
                         .order_by(S.daily_facts.c.day)).fetchall()
    return [json.loads(r[0]) for r in rows]
