"""Patient-day rows built from raw events. Same columns and rules as the training table (echos/data/patient_day.py), so the same models score them."""
from __future__ import annotations

import numpy as np
import pandas as pd

from echos.agents import data_quality as dq
from echos.config import load_config
from echos.data.patient_day import PHYS

COLS = ["patient_id", "day", "has_reading", "sys", "dia", "hr", "temp", "weight",
        "days_since_last_reading", "completeness", "n_invalid", "invalid_json", "source"]


def build_row(patient_id: str, day: pd.Timestamp, vit: pd.DataFrame, hist: pd.DataFrame) -> dict:
    """One patient-day row from the events of that day. `hist` = this patient's earlier rows (training table before the live start + closed live days).
    Ranges: out-of-range readings are set to NaN and listed (never imputed), as in Agent 0."""
    cfg = load_config()
    d = pd.Timestamp(day).normalize()
    row = {c: np.nan for c in COLS}
    row.update(patient_id=patient_id, day=d, source="live")
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
    # days since the last reading, counted over history
    last = hist[(hist.day < d) & hist.has_reading].day.max() if len(hist) else pd.NaT
    first = hist.day.min() if len(hist) else d
    row["days_since_last_reading"] = 0 if row["has_reading"] else int((d - last).days if pd.notna(last) else (d - first).days + 1)
    row["completeness"] = dq.completeness(row["has_reading"], row["n_invalid"])
    return row


def history_for(parquet: pd.DataFrame, closed_live: pd.DataFrame, patient_id: str, day: pd.Timestamp) -> pd.DataFrame:
    """Past rows only: training-table rows before the first live day plus the closed live days (all strictly before `day`)."""
    d = pd.Timestamp(day).normalize()
    p = parquet[(parquet.patient_id == patient_id) & (parquet.day < d)]
    if len(closed_live):
        c = closed_live[(closed_live.patient_id == patient_id) & (closed_live.day < d)]
        p = pd.concat([p[~p.day.isin(c.day)], c], ignore_index=True, sort=False)
    p = p.sort_values("day").reset_index(drop=True)
    for col, default in (("has_reading", False),):
        if col in p:
            p[col] = p[col].fillna(default).astype(bool)
    return p
