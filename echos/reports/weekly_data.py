"""Weekly clinician report data: a PURE function of saved pipeline outputs for one patient and one ISO week (Mon-Sun).
Sources: case_state (arbiter status per day), daily_facts (flags, falls, quality, per-day vitals), alerts (fall payloads: probability, peak g,
inactivity), the patient-day table (routine values; personal baselines use only days BEFORE each day) and raw vitals readings.
No model score is returned for behaviour or vitals: only values against the personal baseline / personal z-scores.
Everything in the result except `series` is plain JSON (the narrative's structured input)."""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Optional

import pandas as pd
from sqlalchemy import select

from echos.config import load_config
from echos.orchestrator import state as S
from echos.reports import facts as F

VARS = {"sys": ("Systolic BP", "mmHg"), "dia": ("Diastolic BP", "mmHg"), "hr": ("Heart rate", "bpm"), "temp": ("Temperature", "C"),
        "weight": ("Weight", "kg")}
DEVICE = {"Systolic blood pressure": "sys", "Diastolic blood pressure": "dia", "Heart rate": "hr", "Body Temperature": "temp", "Body weight": "weight"}
ROUTINE = {"night_events": "Night activity", "night_bathroom": "Night bathroom visits", "total_events": "Total activity", "room_transitions": "Room transitions"}
UNUSUAL = ("agitation", "routine_change")     # fact flag types: reported as 'unusual activity pattern', never with their score


_CACHE: dict = {}


def week_bounds(week) -> tuple[pd.Timestamp, pd.Timestamp]:
    """'2019-W19', a date, or a Timestamp (any day of the week) -> (Monday, Sunday)."""
    if isinstance(week, str) and "-W" in week:
        y, w = week.split("-W")
        mon = pd.Timestamp.fromisocalendar(int(y), int(w), 1)
    else:
        d = pd.Timestamp(week).normalize()
        mon = d - timedelta(days=d.weekday())
    return mon, mon + timedelta(days=6)


def iso_week(day) -> str:
    y, w, _ = pd.Timestamp(day).isocalendar()
    return f"{y}-W{w:02d}"


def _r(x, nd=1):
    return None if x is None or pd.isna(x) else round(float(x), nd)


MIN_VITAL_HISTORY = 5      # earlier readings (patient-days with a reading) needed before a personal vitals baseline exists
NOT_ESTABLISHED = "personal baseline not yet established (fewer than 5 earlier readings)"
ROUTINE_NOT_ESTABLISHED = "personal baseline not yet established (fewer than 7 earlier days)"


def fmt_prob(p) -> str:
    """Fall probability for people: values that round to 1.00 print as '>0.99'."""
    if p is None:
        return "n/a"
    return ">0.99" if round(float(p), 2) >= 1.0 else f"{float(p):.2f}"


def _band(pdf: pd.DataFrame, var: str, day: pd.Timestamp, cfg: dict):
    """Personal band for `day` from PAST days only: (mean, sd, lo, hi), or None with fewer than MIN_VITAL_HISTORY earlier readings."""
    b, sd = F._past_baseline(pdf, var, day, cfg["agent1"]["baseline_window_days"], MIN_VITAL_HISTORY)
    if b is None:
        return None
    sd = max(sd or 0.0, 0.02 * abs(b))
    k = cfg.get("weekly", {}).get("band_sd", 2.0)
    return b, sd, b - k * sd, b + k * sd


def _longest_run(flags: list[bool]) -> int:
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    return best


def build_weekly_data(eng, patient_id: str, week, pdy: Optional[pd.DataFrame] = None, phys: Optional[pd.DataFrame] = None,
                      demo: Optional[pd.DataFrame] = None) -> dict:
    cfg = load_config()
    mon, sun = week_bounds(week)
    days = [mon + timedelta(days=i) for i in range(7)]
    dstr = [d.strftime("%Y-%m-%d") for d in days]
    pdy = F.patient_days() if pdy is None else pdy
    pdf = pdy[pdy.patient_id == patient_id].sort_values("day")

    # ---- saved pipeline outputs
    with eng.begin() as c:
        cs = {r.day: r for r in c.execute(select(S.case_state).where((S.case_state.c.patient_id == patient_id) & (S.case_state.c.day >= dstr[0]) &
                                                                      (S.case_state.c.day <= dstr[-1])))}
        fx = {r.day: json.loads(r.facts) for r in c.execute(select(S.daily_facts).where((S.daily_facts.c.patient_id == patient_id) &
                                                                                         (S.daily_facts.c.day >= dstr[0]) & (S.daily_facts.c.day <= dstr[-1])))}
        al = [r for r in c.execute(select(S.alerts).where((S.alerts.c.patient_id == patient_id) & (S.alerts.c.type == "fall")))
              if r.event_time and dstr[0] <= r.event_time[:10] <= dstr[-1]]
    status = {d: (cs[d].status if d in cs else None) for d in dstr}
    in_pdy = set(pdf[(pdf.day >= mon) & (pdf.day <= sun)].day.dt.strftime("%Y-%m-%d"))
    with_data = [d for d in dstr if d in cs or d in fx or d in in_pdy]
    counts = {k: sum(v == k for v in status.values()) for k in ("NORMAL", "MONITOR", "URGENT")}
    counts["DATA_GAP"] = sum(bool(cs[d].data_gap) for d in cs)
    counts["NOT_PROCESSED"] = sum(1 for d in with_data if status[d] is None)

    # ---- vitals: readings against the personal band
    if phys is None:
        if "phys" not in _CACHE:
            from echos.data.loaders import load_physiology
            _CACHE["phys"] = load_physiology()
        phys = _CACHE["phys"]
    ph = phys[(phys.patient_id == patient_id) & phys.device_type.isin(DEVICE)].copy()
    ph["date"] = pd.to_datetime(ph["date"])
    ph = ph[(ph.date >= mon) & (ph.date < sun + timedelta(days=1))].sort_values("date")
    ranges = cfg["data_quality"]["ranges"]
    ok = [ranges.get(dev, (-1e9, 1e9))[0] <= v <= ranges.get(dev, (-1e9, 1e9))[1] for dev, v in zip(ph.device_type, ph.value)]
    ph = ph[pd.Series(ok, index=ph.index, dtype=bool)]
    series, vsum = {}, {}
    for var in VARS:
        pts, bands = [], {}
        for r in ph[ph.device_type.map(DEVICE) == var].itertuples():
            day = r.date.normalize()
            if day not in bands:
                bands[day] = _band(pdf, var, day, cfg)
            b = bands[day]
            pts.append({"t": r.date.strftime("%Y-%m-%d %H:%M"), "value": _r(r.value, 1), "base": _r(b[0]) if b else None,
                        "lo": _r(b[2]) if b else None, "hi": _r(b[3]) if b else None,
                        "flagged": bool(b and not (b[2] <= r.value <= b[3]))})
        day_band = {d.strftime("%Y-%m-%d"): _band(pdf, var, d, cfg) for d in days}
        series[var] = {"points": pts, "band": {d: ([_r(b[2]), _r(b[3]), _r(b[0])] if b else None) for d, b in day_band.items()}}
        v = [p["value"] for p in pts]
        bs = [p["base"] for p in pts if p["base"] is not None]
        est = any(b is not None for b in day_band.values())
        vsum[var] = {"name": VARS[var][0], "unit": VARS[var][1], "n_readings": len(v), "mean": _r(sum(v) / len(v)) if v else None,
                     "baseline_established": est, "n_with_baseline": len(bs), "baseline": _r(sum(bs) / len(bs)) if bs else None,
                     "n_flagged": sum(p["flagged"] for p in pts) if est else None,        # no 'outside the band' count without a baseline
                     "flagged_days": sorted({p["t"][:10] for p in pts if p["flagged"]}),
                     "note": None if est else NOT_ESTABLISHED}

    # ---- routine: values and personal z-scores (past-only baseline, active days only)
    rw = cfg["agent2"]["baseline_window_days"]
    routine, rz = {k: {"values": {}, "band": {}} for k in ROUTINE}, {}
    for d in days:
        ds = d.strftime("%Y-%m-%d")
        row = pdf[pdf.day == d]
        act = bool(len(row) and row.iloc[0].has_activity)
        z = {}
        for k in ROUTINE:
            val = _r(row.iloc[0][k], 0) if act else None
            b, sd = F._past_baseline(pdf, k, d, rw, cfg["agent2"]["min_baseline_days"], True)
            routine[k]["values"][ds] = val
            routine[k]["band"][ds] = [_r(b - 2 * sd), _r(b + 2 * sd), _r(b)] if b is not None and sd is not None else None
            z[k] = _r((val - b) / sd, 1) if val is not None and b is not None and sd and sd > 0 else None
        rz[ds] = z

    # ---- flags from the saved daily facts
    vit_alerts, unusual = [], []
    for d in dstr:
        for f in (fx.get(d) or {}).get("flags", []):
            e = f["evidence"]
            if e.get("kind") == "critical":
                vit_alerts.append({"day": d, "kind": "critical", "sys": e.get("sys"), "dia": e.get("dia"), "hr": e.get("hr")})
            elif e.get("kind") == "vitals_alert":
                vit_alerts.append({"day": d, "kind": "alert", **{f"{k}_{s}": e.get(f"{k}_{s}") for k in ("sys", "dia", "hr") for s in ("now", "baseline")}})
            elif f["type"] in UNUSUAL and d not in {u["day"] for u in unusual}:
                unusual.append({"day": d, "z": rz[d]})     # z-scores only: the model score is never carried into this report
    # ---- falls: alert payloads (peak g) plus fall facts without an alert (no peak available)
    falls, seen = [], set()
    for r in sorted(al, key=lambda r: r.event_time):
        p = json.loads(r.payload)
        falls.append({"day": r.event_time[:10], "time": r.event_time[11:16], "probability": p.get("fall_probability"),
                      "probability_text": fmt_prob(p.get("fall_probability")), "peak_g": p.get("peak_impact_g"),
                      "inactivity": p.get("post_fall_inactivity")})
        seen.add((r.event_time[:10], r.event_time[11:16]))
    for d in dstr:
        for f in (fx.get(d) or {}).get("falls", []):
            if (d, f.get("time")) not in seen:
                falls.append({"day": d, "time": f.get("time"), "probability": f.get("prob"), "probability_text": fmt_prob(f.get("prob")), "peak_g": None, "inactivity": f.get("post_fall_inactive")})
    falls.sort(key=lambda f: (f["day"], f["time"] or ""))

    # ---- completeness
    has_vit = [bool(d in fx and fx[d].get("has_reading")) or (d not in fx and bool(len(pdf[(pdf.day == pd.Timestamp(d))]) and pdf[pdf.day == pd.Timestamp(d)].iloc[0].has_reading)) for d in dstr]
    has_act = [bool(d in fx and fx[d].get("has_activity")) or (d not in fx and bool(len(pdf[(pdf.day == pd.Timestamp(d))]) and pdf[pdf.day == pd.Timestamp(d)].iloc[0].has_activity)) for d in dstr]
    inv = [i for d in dstr for i in ((fx.get(d) or {}).get("quality") or {}).get("invalid_values", [])]
    gaps = [bool((fx.get(d) or {}).get("quality", {}).get("sensor_gap")) for d in dstr]
    comp = {"days_with_data": len(with_data), "days_with_activity": sum(has_act), "days_with_vitals": sum(has_vit), "sensor_gap_days": sum(gaps),
            "invalid_values_removed": len(inv), "longest_vitals_gap_days": _longest_run([not v for v, d in zip(has_vit, dstr) if d in with_data])}

    demo = __import__("echos.data.loaders", fromlist=["x"]).load_demographics() if demo is None else demo
    dm = demo[demo.patient_id == patient_id]
    import re
    nums = re.findall(r"\d+", str(dm.age_band.iloc[0])) if len(dm) else []
    return {"patient_id": patient_id, "week": iso_week(mon), "days_in_week": 7, "week_start": dstr[0], "week_end": dstr[-1],
            "age_band": f"{nums[0]}-{nums[1]}" if len(nums) >= 2 else "unknown", "sex": str(dm.sex.iloc[0]) if len(dm) else "unknown",
            "days": [{"day": d, "status": status[d], "data_gap": bool(d in cs and cs[d].data_gap), "has_data": d in with_data} for d in dstr],
            "status_counts": counts, "vitals_summary": vsum, "vitals_alerts": vit_alerts, "falls": falls, "unusual_days": unusual,
            "routine_baseline_established": {k: any(b is not None for b in routine[k]["band"].values()) for k in ROUTINE},
            "routine_z": {d: z for d, z in rz.items() if d in {u["day"] for u in unusual}}, "completeness": comp,
            "partial_week_note": (f"Partial week: only {len(with_data)} of 7 days have data." if len(with_data) < 7 else None),
            "any_monitor_or_urgent": bool(counts["MONITOR"] or counts["URGENT"]),
            "finding_types": sorted({f["type"] for d in dstr for f in (fx.get(d) or {}).get("flags", []) if f["type"] != "data_gap"}),
            "series": {"vitals": series, "routine": routine}}


def narrative_payload(data: dict) -> dict:
    """The structured input for the narrative: everything except the plotting series."""
    return {k: v for k, v in data.items() if k != "series"}
