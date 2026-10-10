from __future__ import annotations

import pandas as pd

from echos.config import ROOT, load_config
from echos.data import loaders

ROOMS = ["Kitchen", "Hallway", "Lounge", "Bedroom", "Fridge Door", "Bathroom", "Front Door", "Back Door"]
PHYS = {"Systolic blood pressure": "sys", "Diastolic blood pressure": "dia", "Heart rate": "hr",
        "Body Temperature": "temp", "Body weight": "weight"}
LABELS = {"Blood pressure": "bp_alert", "Pulse": "pulse_alert", "Body water": "water_alert",
          "Agitation": "agitation"}


def _activity_features(act: pd.DataFrame, night_start: int, night_end: int) -> pd.DataFrame:
    act = act.sort_values(["patient_id", "date"]).copy()
    hr = act["date"].dt.hour
    act["night"] = (hr >= night_start) | (hr < night_end)
    act["moved"] = (act["location_name"] != act.groupby(["patient_id", "day"])["location_name"].shift()) \
        & act.groupby(["patient_id", "day"])["location_name"].shift().notna()
    g = ["patient_id", "day"]
    counts = act.pivot_table(index=g, columns="location_name", values="date", aggfunc="size", fill_value=0)
    counts = counts.reindex(columns=ROOMS, fill_value=0)
    counts.columns = ["n_" + c.lower().replace(" ", "_") for c in counts.columns]
    out = counts.copy()
    out["total_events"] = counts.sum(axis=1)
    out["night_events"] = act[act.night].groupby(g).size().reindex(out.index, fill_value=0)
    out["night_bathroom"] = act[act.night & (act.location_name == "Bathroom")].groupby(g).size() \
        .reindex(out.index, fill_value=0)
    out["night_door"] = act[act.night & act.location_name.isin(["Front Door", "Back Door"])].groupby(g).size() \
        .reindex(out.index, fill_value=0)
    out["room_transitions"] = act.groupby(g)["moved"].sum().reindex(out.index, fill_value=0).astype(int)
    out["has_activity"] = True
    return out.reset_index()


def build_patient_day() -> pd.DataFrame:
    cfg = load_config()
    nh = cfg["night_hours"]
    act = loaders.load_activity()
    phys = loaders.load_physiology()
    lab = loaders.load_labels()
    demo = loaders.load_demographics()
    keys = ["patient_id", "day"]

    feats = _activity_features(act, nh["start"], nh["end"])

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
    other = lab[~lab.type.isin(LABELS)][keys].drop_duplicates().assign(other_label=1)

    df = feats.merge(pd_phys, on=keys, how="outer").merge(pd_lab, on=keys, how="outer") \
        .merge(other, on=keys, how="outer")
    act_cols = [c for c in feats.columns if c not in keys + ["has_activity"]]
    df[act_cols] = df[act_cols].fillna(0)
    df["has_activity"] = df["has_activity"].eq(True)
    df["has_reading"] = df[["sys", "dia", "hr"]].notna().any(axis=1)  # vitals reading that day
    flag_cols = list(LABELS.values()) + ["other_label"]
    df[flag_cols] = df[flag_cols].fillna(0).astype(int)
    df = df.merge(demo, on="patient_id", how="left")
    df = df.sort_values(keys).reset_index(drop=True)
    return df


def save_patient_day() -> pd.DataFrame:
    df = build_patient_day()
    path = ROOT / load_config()["paths"]["processed"]
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return df


def load_patient_day() -> pd.DataFrame:
    return pd.read_parquet(ROOT / load_config()["paths"]["processed"])


if __name__ == "__main__":
    d = save_patient_day()
    print(d.shape, d.patient_id.nunique(), int(d.has_reading.sum()),
          d[["bp_alert", "pulse_alert", "water_alert", "agitation"]].sum().to_dict())
