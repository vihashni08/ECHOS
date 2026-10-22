from __future__ import annotations

import pandas as pd

from echos.config import ROOT, load_config


def _dir():
    return ROOT / load_config()["paths"]["tihm"]


def _with_day(df: pd.DataFrame) -> pd.DataFrame:
    df["date"] = pd.to_datetime(df["date"])
    df["day"] = df["date"].dt.normalize()
    return df


def load_physiology() -> pd.DataFrame:
    return _with_day(pd.read_csv(_dir() / "Physiology.csv"))


def load_labels() -> pd.DataFrame:
    return _with_day(pd.read_csv(_dir() / "Labels.csv"))


def load_demographics() -> pd.DataFrame:
    return pd.read_csv(_dir() / "Demographics.csv").rename(columns={"age": "age_band"})


def load_activity() -> pd.DataFrame:
    """Room / door sensor events (patient_id, location_name, date). ~1M rows; used by the v2 agitation detector only."""
    df = pd.read_csv(_dir() / "Activity.csv")
    df["date"] = pd.to_datetime(df["date"])
    return df
