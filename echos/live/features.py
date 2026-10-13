"""Patient-day rows built from raw events. Same columns and rules as the training table (echos/data/patient_day.py), so the same models score them."""
from __future__ import annotations

import numpy as np
import pandas as pd

from echos.agents import data_quality as dq
from echos.config import load_config
from echos.data.patient_day import PHYS, ROOMS, _activity_features

COLS = ["patient_id", "day", "has_activity", "has_reading", "sys", "dia", "hr", "temp", "weight", "sensor_gap", "sensor_gap_reason",
        "days_since_last_reading", "completeness", "n_invalid", "invalid_json", "source"]


def build_row(patient_id: str, day: pd.Timestamp, act: pd.DataFrame, vit: pd.DataFrame, hist: pd.DataFrame, closed: bool) -> dict:
    """One patient-day row from the events of that day. `hist` = this patient's earlier rows (training table before the live start + closed live days).
    Ranges: out-of-range readings are set to NaN and listed (never imputed), as in Agent 0. The sensor-gap rule needs the whole day: it is only applied
    when `closed`; an open day reports sensor silence through the Quality gate's silent-hours rule instead."""
    cfg = load_config()
    nh = cfg["night_hours"]
    d = pd.Timestamp(day).normalize()
    row = {c: np.nan for c in COLS}
    row.update(patient_id=patient_id, day=d, source="live")
    n_cols = ["n_" + r.lower().replace(" ", "_") for r in ROOMS] + ["total_events", "night_events", "night_bathroom", "night_door", "room_transitions"]
    if len(act):
        f = _activity_features(act.assign(day=act.date.dt.normalize()), nh["start"], nh["end"]).iloc[0]
        row.update({c: float(f[c]) for c in n_cols})
        row["has_activity"] = True
    else:
        row.update({c: 0.0 for c in n_cols})
        row["has_activity"] = False
    invalid = []
    ranges = cfg["data_quality"]["ranges"]
    for dev, var in PHYS.items():
        x = vit[vit.device_type == dev].value
        if len(x):
            lo, hi = ranges.get(dev, [-np.inf, np.inf])
            ok = x[(x >= lo) & (x <= hi)]
            invalid += [{"device_type": dev, "value": float(v), "range": [lo, hi], "reason": "out_of_range"} for v in x[(x < lo) | (x > hi)]]
            row[var] = float(ok.mean()) if len(ok) else np.nan
    row["has_reading"] = bool(any(not pd.isna(row[k]) for k in ("sys", "dia", "hr")))
    import json
    row["invalid_json"] = json.dumps(invalid) if invalid else ""
    row["n_invalid"] = len(invalid)
    # sensor gap (closed days only): zero events, or fewer than frac x the median of the PAST non-gap days
    gap, why = False, None
    if closed:
        dqc = cfg["data_quality"]
        n = row["total_events"]
        past = hist[(hist.day < d) & hist.has_activity & ~hist.sensor_gap.fillna(False).astype(bool)].total_events.tail(60) if len(hist) else pd.Series(dtype=float)
        med = float(np.median(past)) if len(past) >= dqc["min_history_days"] else np.nan
        if n == 0:
            gap, why = True, "zero_events"
        elif not np.isnan(med) and n < dqc["sensor_gap_frac"] * med:
            gap, why = True, "low_events"
    row["sensor_gap"], row["sensor_gap_reason"] = gap, why
    if gap:                                  # a gap is not "zero activity": behaviour features stay NaN, as in the training table
        for c in n_cols:
            row[c] = np.nan
        row["has_activity"] = False
    # days since the last reading, counted over history
    last = hist[(hist.day < d) & hist.has_reading].day.max() if len(hist) else pd.NaT
    first = hist.day.min() if len(hist) else d
    row["days_since_last_reading"] = 0 if row["has_reading"] else int((d - last).days if pd.notna(last) else (d - first).days + 1)
    row["completeness"] = dq.completeness(gap, row["has_reading"], row["n_invalid"])
    return row


def history_for(parquet: pd.DataFrame, closed_live: pd.DataFrame, patient_id: str, day: pd.Timestamp) -> pd.DataFrame:
    """Past rows only: training-table rows before the first live day plus the closed live days (all strictly before `day`)."""
    d = pd.Timestamp(day).normalize()
    p = parquet[(parquet.patient_id == patient_id) & (parquet.day < d)]
    if len(closed_live):
        c = closed_live[(closed_live.patient_id == patient_id) & (closed_live.day < d)]
        p = pd.concat([p[~p.day.isin(c.day)], c], ignore_index=True, sort=False)
    p = p.sort_values("day").reset_index(drop=True)
    for col, default in (("sensor_gap", False), ("has_activity", False), ("has_reading", False)):
        if col in p:
            p[col] = p[col].fillna(default).astype(bool)
    return p
