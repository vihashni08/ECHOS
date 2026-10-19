"""Agent 0 - Data quality. RULE-BASED (no ML); runs before Agents 1-3. Policy: docs/DATA_POLICY.md.

1. validate schema/types, drop exact duplicates, sort by (patient, timestamp)
2. range checks per device_type: implausible values -> NaN and recorded as invalid (never dropped silently, never imputed)
3. missing data: vitals stay NaN (has_reading, days_since_last_reading); an Activity day with zero events or fewer than
   5% of the patient's PAST median is a SENSOR_GAP, not 'zero activity': behaviour features are NaN for that day
4. one DataQualityResult per patient-day
Failure of this agent never blocks the pipeline: `check_day` falls back to a pass-through result with quality_checked=False."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from echos.config import load_config
from echos.schemas import AgentResult, DataQualityResult

AGENT = "quality"
ROOMS = ["Kitchen", "Hallway", "Lounge", "Bedroom", "Fridge Door", "Bathroom", "Front Door", "Back Door"]
PHYS_COLS = ["patient_id", "date", "device_type", "value", "unit"]
ACT_COLS = ["patient_id", "location_name", "date"]


class DataQualityError(ValueError):
    pass


def _need(df: pd.DataFrame, cols: list, name: str):
    miss = [c for c in cols if c not in df.columns]
    if miss:
        raise DataQualityError(f"{name}: missing columns {miss}")


def clean_physiology(phy: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Returns (clean, invalid, stats). `clean` keeps every row; invalid values are NaN there. `invalid` lists them."""
    _need(phy, PHYS_COLS, "Physiology")
    ranges = load_config()["data_quality"]["ranges"]
    df = phy.copy()
    n0 = len(df)
    df["patient_id"] = df["patient_id"].astype(str)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    bad_ts = int(df["date"].isna().sum())
    df = df[df["date"].notna()].copy()
    num = pd.to_numeric(df["value"], errors="coerce")
    nonnum = df["value"].notna() & num.isna()
    nn_rows = df.loc[nonnum, ["patient_id", "date", "device_type", "value"]].assign(reason="non_numeric", range="")
    df["value"] = num
    n_dup = int(df.duplicated().sum())
    df = df.drop_duplicates()
    n_ooo = int((df.groupby("patient_id")["date"].diff() < pd.Timedelta(0)).sum())
    df = df.sort_values(["patient_id", "date", "device_type"], kind="stable").reset_index(drop=True)
    df["day"] = df["date"].dt.normalize()
    inv = []
    for dev, (lo, hi) in ranges.items():
        m = (df.device_type == dev) & df.value.notna() & ((df.value < lo) | (df.value > hi))
        if m.any():
            r = df.loc[m, ["patient_id", "day", "date", "device_type", "value"]].copy()
            r["range"] = f"{lo}-{hi}"
            r["reason"] = "out_of_range"
            inv.append(r)
            df.loc[m, "value"] = np.nan
    if len(nn_rows):
        inv.append(nn_rows.assign(day=nn_rows["date"].dt.normalize()))
    invalid = pd.concat(inv).reset_index(drop=True) if inv else pd.DataFrame(
        columns=["patient_id", "day", "date", "device_type", "value", "range", "reason"])
    stats = {"rows_in": n0, "bad_timestamp_dropped": bad_ts, "exact_duplicates_dropped": n_dup, "out_of_order_rows_resorted": n_ooo,
             "invalid_values": int(len(invalid)), "invalid_by_device": invalid.groupby("device_type").size().to_dict()}
    return df, invalid, stats


def clean_activity(act: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    _need(act, ACT_COLS, "Activity")
    df = act.copy()
    n0 = len(df)
    df["patient_id"] = df["patient_id"].astype(str)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    bad_ts = int(df["date"].isna().sum())
    df = df[df["date"].notna()]
    unk = int((~df.location_name.isin(ROOMS)).sum())
    df = df[df.location_name.isin(ROOMS)]
    n_dup = int(df.duplicated(ACT_COLS).sum())
    df = df.drop_duplicates(ACT_COLS)
    n_ooo = int((df.groupby("patient_id")["date"].diff() < pd.Timedelta(0)).sum())
    df = df.sort_values(["patient_id", "date"], kind="stable").reset_index(drop=True)
    df["day"] = df["date"].dt.normalize()
    return df, {"rows_in": n0, "bad_timestamp_dropped": bad_ts, "unknown_room_dropped": unk, "exact_duplicates_dropped": n_dup,
                "out_of_order_rows_resorted": n_ooo}


def sensor_gap_table(act: pd.DataFrame, frac: Optional[float] = None, min_hist: Optional[int] = None) -> pd.DataFrame:
    """Per patient, every calendar day between the first and last activity day: events, gap flag and reason.
    Rule: zero events -> gap; else events < frac * median(PAST non-gap daily counts) once >= min_hist past good days exist.
    Only days before `day` enter the median (no leakage); gap days are excluded from it (a gap must not lower the bar)."""
    dq = load_config()["data_quality"]
    frac = dq["sensor_gap_frac"] if frac is None else frac
    min_hist = dq["min_history_days"] if min_hist is None else min_hist
    cnt = act.groupby(["patient_id", "day"]).size().rename("n_events").reset_index()
    out = []
    for p, g in cnt.groupby("patient_id"):
        cal = pd.date_range(g.day.min(), g.day.max())
        n = g.set_index("day").n_events.reindex(cal).fillna(0).astype(int)
        good, gap, why, med = [], [], [], []
        for v in n.values:
            m = float(np.median(good)) if len(good) >= min_hist else np.nan
            med.append(m)
            if v == 0:
                gap.append(True), why.append("zero_events")
            elif not np.isnan(m) and v < frac * m:
                gap.append(True), why.append("low_events")
            else:
                gap.append(False), why.append(None)
                good.append(v)
        out.append(pd.DataFrame({"patient_id": p, "day": cal, "n_events": n.values, "past_median": med,
                                 "sensor_gap": gap, "sensor_gap_reason": why}))
    return pd.concat(out).reset_index(drop=True) if out else pd.DataFrame(
        columns=["patient_id", "day", "n_events", "past_median", "sensor_gap", "sensor_gap_reason"])


def days_since_reading(df: pd.DataFrame) -> pd.Series:
    """0 if a reading exists that day; else days since the last PAST reading; never read -> days since first record + 1."""
    res = pd.Series(index=df.index, dtype="float")
    for _, g in df.sort_values("day").groupby("patient_id"):
        first, last = g.day.min(), None
        for i, d, r in zip(g.index, g.day, g.has_reading):
            if r:
                res[i], last = 0, d
            else:
                res[i] = (d - last).days if last is not None else (d - first).days + 1
    return res.astype(int)


def completeness(sensor_gap: bool, has_vitals: bool, n_invalid: int) -> float:
    w = load_config()["data_quality"]["weights"]
    return round(w["activity"] * (not sensor_gap) + w["vitals"] * bool(has_vitals) + w["validity"] * (n_invalid == 0), 3)


# ---------------------------------------------------------------- runtime (Agent 0 worker)
_cache: dict = {}


def _state() -> pd.DataFrame:
    if "df" not in _cache:
        from echos.data.patient_day import load_patient_day
        _cache["df"] = load_patient_day().set_index(["patient_id", "day"])
    return _cache["df"]


def unchecked(patient_id: str, day, reason: str) -> AgentResult:
    """Pass-through fallback when Agent 0 fails: data goes on unchecked and is flagged as such."""
    d = pd.Timestamp(day).date()
    dq = DataQualityResult(patient_id=patient_id, day=d, has_vitals=False, completeness=0.0, quality_checked=False)
    return AgentResult(patient_id=patient_id, day=d, agent=AGENT, score=1.0, flag=True,
                       evidence={**dq.model_dump(mode="json"), "reason": f"quality unchecked: {reason}"}, ts=datetime.utcnow())


def check_day(patient_id: str, day) -> AgentResult:
    d = pd.Timestamp(day).normalize()
    df = _state()
    r = df.loc[(patient_id, d)]
    inv = json.loads(r["invalid_json"]) if isinstance(r["invalid_json"], str) and r["invalid_json"] else []
    dq = DataQualityResult(patient_id=patient_id, day=d.date(), has_vitals=bool(r["has_reading"]), invalid_values=inv,
                           sensor_gap=bool(r["sensor_gap"]),
                           sensor_gap_reason=r["sensor_gap_reason"] if isinstance(r["sensor_gap_reason"], str) else None,
                           days_since_last_reading=int(r["days_since_last_reading"]), completeness=float(r["completeness"]))
    bad = dq.sensor_gap or bool(dq.invalid_values)
    return AgentResult(patient_id=patient_id, day=d.date(), agent=AGENT, score=round(1 - dq.completeness, 3), flag=bad,
                       evidence=dq.model_dump(mode="json"), ts=datetime.utcnow())


def predict(patient_id: str, day) -> AgentResult:
    try:
        return check_day(patient_id, day)
    except Exception as ex:  # noqa: BLE001 - Agent 0 must never stop the pipeline
        return unchecked(patient_id, day, type(ex).__name__)
