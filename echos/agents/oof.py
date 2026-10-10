"""Optional out-of-fold score override for replay evaluation: set ECHOS_OOF=1 and Agents 1-2 return their
patient-level GroupKFold out-of-fold score (models/oof_scores.parquet) instead of the in-sample final model's score."""
from __future__ import annotations

import os

import pandas as pd

from echos.config import ROOT

_cache: dict = {}


def enabled() -> bool:
    return os.environ.get("ECHOS_OOF") == "1"


def lookup(agent: str, patient_id: str, day) -> float | None:
    if "s" not in _cache:
        df = pd.read_parquet(ROOT / "models" / "oof_scores.parquet")
        _cache["s"] = {(r.agent, r.patient_id, r.day): r.oof for r in df.itertuples()}
    return _cache["s"].get((agent, patient_id, pd.Timestamp(day).normalize()))
