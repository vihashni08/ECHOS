from __future__ import annotations

from typing import Iterator, Tuple

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold


def patient_folds(df: pd.DataFrame, n_folds: int = 5, seed: int = 42
                  ) -> Iterator[Tuple[np.ndarray, np.ndarray]]:
    """Patient-level GroupKFold. Patients are shuffled by seed, then mapped to
    groups so the split is deterministic yet not alphabetical."""
    pids = np.array(sorted(df["patient_id"].unique()))
    rng = np.random.RandomState(seed)
    perm = dict(zip(pids, rng.permutation(len(pids))))
    groups = df["patient_id"].map(perm).values
    yield from GroupKFold(n_splits=n_folds).split(df, groups=groups)


def subject_folds(df: pd.DataFrame, n_folds: int = 5, seed: int = 42
                  ) -> Iterator[Tuple[np.ndarray, np.ndarray]]:
    """Subject-level GroupKFold for SisFall (column `subject`); no subject spans train and test."""
    subs = np.array(sorted(df["subject"].unique()))
    perm = dict(zip(subs, np.random.RandomState(seed).permutation(len(subs))))
    yield from GroupKFold(n_splits=n_folds).split(df, groups=df["subject"].map(perm).values)
