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

VITAL_VARS = ["sys", "dia", "hr", "temp", "weight"]
ROUTINE_VARS = ["total_events", "night_events", "night_bathroom"]
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
    routine = {}
    for v in ROUTINE_VARS:
        cur = _r(row[v], 0) if has_activity else None
        b, _ = _past_baseline(pdf, v, d, cfg["agent2"]["baseline_window_days"], cfg["agent2"]["min_baseline_days"], True)
        routine[v] = {"value": cur, "baseline": _round(b)}

    phys, beh, fall = (results.get(k) for k in ("physiological", "behaviour", "fall"))
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
            falls.append({"time": ts[11:16] if len(ts) >= 16 else None, "prob": round(r["score"], 2),
                          "post_fall_inactive": bool(r["evidence"].get("post_fall_inactive"))})
    falls.sort(key=lambda f: f["time"] or "")
    for f in falls:
        add("fall_inactivity" if f["post_fall_inactive"] else "fall", **f)

    # ---- vitals
    if phys and "current_vs_baseline" in phys["evidence"]:
        cv = phys["evidence"]["current_vs_baseline"]
        cur = {k: _r(v["current"]) for k, v in cv.items()}
        crit = t["critical_vitals"]
        s_, di_, h_ = cur.get("sys"), cur.get("dia"), cur.get("hr")
        if (s_ is not None and (s_ >= crit["sys_high"] or s_ < crit["sys_low"])) or (di_ is not None and di_ >= crit["dia_high"]) \
                or (h_ is not None and (h_ >= crit["hr_high"] or h_ < crit["hr_low"])):
            add("vitals_news2", kind="critical", sys=s_, dia=di_, hr=h_)
        if phys["score"] >= t["vitals_prob"]:
            z = {k: abs(cv.get(k, {}).get("z") or 0) for k in ("sys", "dia", "hr")}
            add("blood_pressure" if max(z["sys"], z["dia"]) >= z["hr"] else "vitals_news2",
                kind="vitals_alert", risk=round(phys["score"], 2),
                **{f"{k}_now": _r(v["current"]) for k, v in cv.items()},
                **{f"{k}_baseline": _r(v["baseline"]) for k, v in cv.items()})
    # ---- behaviour
    if beh and "reason" not in beh["evidence"]:
        ev = beh["evidence"]
        if beh["score"] >= t["agitation_prob"]:
            add("agitation", risk=round(beh["score"], 2), night_events=routine["night_events"]["value"],
                night_events_baseline=routine["night_events"]["baseline"])
        if ev.get("drifted_features") or ev.get("drift_score", 0) >= t["drift_score"]:
            add("routine_change", features=list(ev.get("drifted_features", [])), drift_score=_r(ev.get("drift_score"), 2))
    # ---- data gaps
    gaps = [r for r in decision.get("reasons", []) if str(r).startswith("data_gap:")]
    for g in gaps:
        add("data_gap", note=str(g)[len("data_gap:"):].strip())

    return {"patient_id": patient_id, "day": day, "status": decision.get("status", "NORMAL"), "data_gap": bool(decision.get("data_gap")),
            "triage_reasons": list(decision.get("reasons", [])),
            "has_reading": bool(row is not None and row["has_reading"]), "has_activity": has_activity,
            "vitals": vitals, "routine": routine, "falls": falls, "flags": flags}


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
