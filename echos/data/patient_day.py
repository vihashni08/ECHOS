from __future__ import annotations

import json

import pandas as pd

from echos.config import ROOT, load_config
from echos.agents import data_quality as dq
from echos.data import loaders

PHYS = {"Systolic blood pressure": "sys", "Diastolic blood pressure": "dia", "Heart rate": "hr",
        "Body Temperature": "temp", "Body weight": "weight"}
LABELS = {"Blood pressure": "bp_alert", "Pulse": "pulse_alert", "Body water": "water_alert"}
UNUSED_LABELS = ("Agitation",)      # present in Labels.csv, not modelled (no behaviour detector)


def _activity_features(act: pd.DataFrame) -> pd.DataFrame:
    """Daily room-sensor event count per patient: the only thing the Data Quality gate needs (sensor-gap rule). Rooms and times are not interpreted."""
    out = act.groupby(["patient_id", "day"]).size().rename("total_events").reset_index()
    out["has_activity"] = True
    return out


def build_patient_day(return_stats: bool = False):
    """Patient-day table built from Agent 0's cleaned data (docs/DATA_POLICY.md)."""
    cfg = load_config()
    act, act_stats = dq.clean_activity(loaders.load_activity())
    phys, invalid, phys_stats = dq.clean_physiology(loaders.load_physiology())
    gaps = dq.sensor_gap_table(act)
    lab = loaders.load_labels()
    demo = loaders.load_demographics()
    keys = ["patient_id", "day"]

    feats = _activity_features(act)

    p = phys[phys.device_type.isin(PHYS)].copy()
    p["var"] = p.device_type.map(PHYS)
    pd_phys = p.pivot_table(index=keys, columns="var", values="value", aggfunc="mean")
    pd_phys = pd_phys.reindex(columns=list(PHYS.values()))
    pd_phys = pd_phys.reset_index()

    l = lab[lab.type.isin(LABELS)].copy()
    l["flag"] = l.type.map(LABELS)
    pd_lab = l.assign(v=1).pivot_table(index=keys, columns="flag", values="v", aggfunc="max", fill_value=0)
    pd_lab = pd_lab.reindex(columns=list(LABELS.values()), fill_value=0).reset_index()
    # Weight / body-temperature labels are too rare to model; kept for completeness
    other = lab[~lab.type.isin(list(LABELS) + list(UNUSED_LABELS))][keys].drop_duplicates().assign(other_label=1)

    df = feats.merge(pd_phys, on=keys, how="outer").merge(pd_lab, on=keys, how="outer") \
        .merge(other, on=keys, how="outer")
    df["total_events"] = df["total_events"].fillna(0)
    df["has_activity"] = df["has_activity"].eq(True)
    df["has_reading"] = df[["sys", "dia", "hr"]].notna().any(axis=1)  # vitals reading that day
    flag_cols = list(LABELS.values()) + ["other_label"]
    df[flag_cols] = df[flag_cols].fillna(0).astype(int)
    df = df.merge(demo, on="patient_id", how="left")
    df = df.sort_values(keys).reset_index(drop=True)
    df["source"] = "tihm"  # training-table provenance (echos/data/guard.py)

    # ---- Agent 0 columns. SENSOR_GAP days keep NaN event counts (not zero) and are not usable activity days.
    df = df.merge(gaps[["patient_id", "day", "sensor_gap", "sensor_gap_reason"]], on=keys, how="left")
    no_rec = df["sensor_gap"].isna()  # day outside the patient's activity window (or no activity record at all)
    df["sensor_gap"] = df["sensor_gap"].fillna(True).astype(bool)
    df["sensor_gap_reason"] = df["sensor_gap_reason"].where(~no_rec, "no_activity_record")
    df.loc[df.sensor_gap & df.has_activity, "sensor_gap_reason"] = df.loc[df.sensor_gap & df.has_activity, "sensor_gap_reason"].fillna("low_events")
    df["has_activity"] = df["has_activity"] & ~df["sensor_gap"]
    df.loc[~df.has_activity, "total_events"] = float("nan")
    df["days_since_last_reading"] = dq.days_since_reading(df)
    inv = invalid.assign(day=pd.to_datetime(invalid["day"]))
    inv_json = inv.groupby(["patient_id", "day"]).apply(
        lambda g: json.dumps([{"device_type": r.device_type, "value": None if pd.isna(r.value) else float(r.value),
                               "range": r.range, "reason": r.reason} for r in g.itertuples()])).rename("invalid_json").reset_index() \
        if len(inv) else pd.DataFrame(columns=["patient_id", "day", "invalid_json"])
    df = df.merge(inv_json, on=keys, how="left")
    df["invalid_json"] = df["invalid_json"].fillna("")
    df["n_invalid"] = df["invalid_json"].map(lambda x: len(json.loads(x)) if x else 0)
    df["completeness"] = [dq.completeness(g, v, n) for g, v, n in zip(df.sensor_gap, df.has_reading, df.n_invalid)]
    if return_stats:
        return df, {"physiology": phys_stats, "activity": act_stats}
    return df


def save_patient_day() -> pd.DataFrame:
    df, stats = build_patient_day(return_stats=True)
    stats["patient_days"] = int(len(df))
    stats["sensor_gap_days"] = int(df.sensor_gap.sum())
    stats["sensor_gap_by_reason"] = df[df.sensor_gap].sensor_gap_reason.value_counts().to_dict()
    (ROOT / "reports/metrics/data_quality.json").write_text(json.dumps(stats, indent=2, default=str))
    path = ROOT / load_config()["paths"]["processed"]
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return df


def load_patient_day() -> pd.DataFrame:
    return pd.read_parquet(ROOT / load_config()["paths"]["processed"])


if __name__ == "__main__":
    d = save_patient_day()
    print(d.shape, d.patient_id.nunique(), int(d.has_reading.sum()),
          d[["bp_alert", "pulse_alert", "water_alert"]].sum().to_dict())
