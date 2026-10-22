"""The data ECHOS v2 uses from TIHM, and nothing else.

Vitals cohort: patients with >= monitor.min_bp_days days of blood-pressure readings. One row per calendar day from a patient's first to
last BP reading (missing days stay as NaN rows so the episode tracker sees the gaps). Columns: systolic / diastolic / heart rate (daily max,
because TIHM's alerts and labels are about the most extreme reading of the day), body temperature / weight / body water (daily mean,
context and safety rules only), and `event` = a validated Blood-pressure or Pulse label that day.

Labels are TIHM alerts that the study's monitoring team confirmed as true (Palermo et al. 2023), so they are confirmed events, not diagnoses."""
from __future__ import annotations

import numpy as np
import pandas as pd

from echos.config import load_config
from echos.data import loaders

VITALS = {"Systolic blood pressure": "sys", "Diastolic blood pressure": "dia", "Heart rate": "hr"}
CONTEXT = {"Body Temperature": "temp", "Body weight": "weight", "Total body water": "water"}
VITAL_LABELS = ("Blood pressure", "Pulse")


def _cfg() -> dict:
    return load_config()["monitor"]


def daily_vitals(phys: pd.DataFrame | None = None) -> pd.DataFrame:
    """(patient_id, day) -> sys, dia, hr (daily max) and temp, weight, water (daily mean)."""
    phys = loaders.load_physiology() if phys is None else phys
    phys = phys[phys.device_type.isin(list(VITALS) + list(CONTEXT))]
    v = phys[phys.device_type.isin(VITALS)].pivot_table(index=["patient_id", "day"], columns="device_type", values="value", aggfunc="max")
    c = phys[phys.device_type.isin(CONTEXT)].pivot_table(index=["patient_id", "day"], columns="device_type", values="value", aggfunc="mean")
    out = v.rename(columns=VITALS).join(c.rename(columns=CONTEXT), how="outer").reset_index()
    for col in list(VITALS.values()) + list(CONTEXT.values()):
        if col not in out:
            out[col] = np.nan
    return out


def vitals_cohort(phys: pd.DataFrame | None = None, labels: pd.DataFrame | None = None, min_bp_days: int | None = None) -> pd.DataFrame:
    """Calendar table for the vitals cohort with the `event` column. Adds `source='tihm'` for the real-data guard."""
    min_bp_days = _cfg()["min_bp_days"] if min_bp_days is None else min_bp_days
    labels = loaders.load_labels() if labels is None else labels
    dv = daily_vitals(phys)
    bp_days = dv.dropna(subset=["sys"]).groupby("patient_id").day.nunique()
    keep = bp_days[bp_days >= min_bp_days].index
    dv = dv[dv.patient_id.isin(keep)]
    parts = []
    for pid, g in dv.groupby("patient_id"):
        bp = g.dropna(subset=["sys"])
        cal = pd.DataFrame({"day": pd.date_range(bp.day.min(), bp.day.max(), freq="D")})
        cal["patient_id"] = pid
        parts.append(cal.merge(g, on=["patient_id", "day"], how="left"))
    d = pd.concat(parts, ignore_index=True)
    ev = labels[labels.type.isin(VITAL_LABELS)][["patient_id", "day"]].drop_duplicates().assign(event=1)
    d = d.merge(ev, on=["patient_id", "day"], how="left")
    d["event"] = d["event"].fillna(0).astype(int)
    d["has_reading"] = d["sys"].notna().astype(int)
    d["source"] = "tihm"
    return d.sort_values(["patient_id", "day"]).reset_index(drop=True)


def add_targets(d: pd.DataFrame, horizon: int | None = None) -> pd.DataFrame:
    """y_ahead = any event on days t+1..t+H (known at the end of day t). Rows whose horizon runs past the patient's last day are dropped."""
    horizon = _cfg()["horizon_days"] if horizon is None else horizon
    d = d.copy()
    g = d.groupby("patient_id")["event"]
    d["y_ahead"] = sum(g.shift(-k) for k in range(1, horizon + 1))
    d = d.dropna(subset=["y_ahead"]).copy()
    d["y_ahead"] = (d["y_ahead"] > 0).astype(int)
    return d.reset_index(drop=True)


def safety_flags(row: dict, cfg: dict | None = None) -> list[str]:
    """TIHM Table 5 limits on one day's readings -> list of reasons (empty = nothing urgent). Missing values never fire."""
    s = (cfg or _cfg())["safety"]
    out = []

    def val(k):
        x = row.get(k)
        return None if x is None or (isinstance(x, float) and np.isnan(x)) else float(x)
    sys_, dia, hr, temp, water = (val(k) for k in ("sys", "dia", "hr", "temp", "water"))
    if sys_ is not None and (sys_ < s["sys_low"] or sys_ > s["sys_high"]):
        out.append(f"systolic {sys_:.0f} outside {s['sys_low']}-{s['sys_high']} mmHg")
    if dia is not None and (dia < s["dia_low"] or dia > s["dia_high"]):
        out.append(f"diastolic {dia:.0f} outside {s['dia_low']}-{s['dia_high']} mmHg")
    if hr is not None and (hr < s["hr_low"] or hr > s["hr_high"]):
        out.append(f"pulse {hr:.0f} outside {s['hr_low']}-{s['hr_high']} bpm")
    if temp is not None and (temp < s["temp_low"] or temp > s["temp_high"]):
        out.append(f"temperature {temp:.1f} outside {s['temp_low']}-{s['temp_high']} C")
    if water is not None and (water < s["water_low"] or water > s["water_high"]):
        out.append(f"body water {water:.0f}% outside {s['water_low']}-{s['water_high']}%")
    return out
