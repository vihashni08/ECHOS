"""SisFall loader. File format: 9 comma-separated ints per row ending in ';', 200 Hz.
Cols 1-3 ADXL345 accel (raw; 13-bit, +-16 g -> g = raw*32/2**13), 4-6 ITG3200 gyro, 7-9 MMA8451Q accel.
Name: <D|F><nn>_<SA|SE><nn>_R<nn>.txt; SA = adults, SE = elderly."""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pandas as pd

from echos.config import ROOT, load_config

SISFALL_DIR = ROOT / "data" / "sisfall" / "SisFall_dataset"
G_PER_COUNT = 32.0 / 2 ** 13


def load_file(path) -> np.ndarray:
    """Return the first accelerometer as (n, 3) float array in g."""
    txt = Path(path).read_text().replace(";", "")
    a = pd.read_csv(io.StringIO(txt), header=None, usecols=[0, 1, 2]).values.astype(float)
    return a * G_PER_COUNT


def list_files() -> pd.DataFrame:
    rows = []
    for p in sorted(SISFALL_DIR.glob("S*/*.txt")):
        act, subj, _ = p.stem.split("_")
        rows.append({"path": str(p), "activity": act, "subject": subj, "group": "adult" if subj[:2] == "SA" else "elderly",
                     "is_fall": act[0] == "F"})
    return pd.DataFrame(rows)


def svm(a: np.ndarray) -> np.ndarray:
    return np.sqrt((a ** 2).sum(axis=1))


def fall_window(a: np.ndarray, n: int) -> np.ndarray | None:
    """Window of n samples centred on the impact peak (max SVM)."""
    pk = int(np.argmax(svm(a)))
    s = pk - n // 2
    return a[s:s + n] if s >= 0 and s + n <= len(a) else None


def sliding_windows(a: np.ndarray, n: int, stride: int):
    for s in range(0, len(a) - n + 1, stride):
        yield s, a[s:s + n]
