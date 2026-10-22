"""Synthetic-data guard: models train only on real data. Every training table carries a `source` column
('tihm'); trainers call assert_real_source() and fail on anything else (simulated, synthetic, missing)."""
from __future__ import annotations

import pandas as pd

REAL_SOURCES = {"tihm"}


def assert_real_source(df: pd.DataFrame, expected: str) -> None:
    if expected not in REAL_SOURCES:
        raise ValueError(f"unknown training source {expected!r}")
    if "source" not in df.columns:
        raise AssertionError(f"training table has no 'source' column (expected all rows == {expected!r})")
    bad = sorted(set(df["source"].astype(str)) - {expected})
    if bad:
        raise AssertionError(f"non-{expected} rows in a training table (sources: {bad}); synthetic/simulated data must never be trained on")
