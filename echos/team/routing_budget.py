"""Call-budget choice of the Triage stage-2 cutoff theta (label-free). Pure functions, used by scripts/choose_routing_threshold.py and the tests.

A "false call" is a watch call on a day where NO rule fired (status NORMAL): the nearest-threshold ratio r exceeded theta anyway.
theta is the smallest value such that false calls per patient-week <= B, i.e. the (1 - B*pw/n_normal) quantile of r on Normal days (strict '>' gate, so ties
at the cutoff are NOT called)."""
from __future__ import annotations

import math
from typing import Optional, Sequence

import numpy as np


def allowed_calls(budget_per_patient_week: float, patient_days: int) -> int:
    """Whole calls the budget allows: floor(B * patient-days / 7) (patient-weeks = ALL patient-days / 7, as choose_monitor_thresholds.py)."""
    return int(math.floor(budget_per_patient_week * patient_days / 7.0 + 1e-9))


def budget_theta(p_normal: Sequence[float], budget_per_patient_week: float, patient_days: int) -> float:
    """Smallest theta with  #(P > theta) <= allowed calls.  Budget >= every Normal day -> 0.0 (every day with P > 0 is called);
    budget allows zero calls -> max(P) (nothing exceeds it). Rounded UP to 4 decimals so the rounded value keeps the budget."""
    p = np.sort(np.asarray(p_normal, dtype=float))[::-1]
    k = allowed_calls(budget_per_patient_week, patient_days)
    if len(p) == 0 or k >= len(p):
        return 0.0
    theta = float(p[k])                      # k = 0 -> the largest value; else the (k+1)-th largest: P > theta holds for at most k days
    return math.ceil(theta * 1e4 - 1e-9) / 1e4


def false_calls(p_normal: Sequence[float], theta: float) -> int:
    return int((np.asarray(p_normal, dtype=float) > theta).sum())


def bootstrap_theta(p: np.ndarray, patient: np.ndarray, is_normal: np.ndarray, budget: float, n_boot: int, seed: int) -> Optional[dict]:
    """Resample PATIENTS with replacement; per replicate theta on that sample's Normal days with its own patient-day count. -> 95% interval (2.5 / 97.5 percentile)."""
    rng = np.random.default_rng(seed)
    ids = np.unique(patient)
    rows = {i: np.flatnonzero(patient == i) for i in ids}
    out = []
    for _ in range(n_boot):
        idx = np.concatenate([rows[i] for i in rng.choice(ids, size=len(ids), replace=True)])
        out.append(budget_theta(p[idx][is_normal[idx]], budget, len(idx)))
    lo, med, hi = np.percentile(out, [2.5, 50, 97.5])
    return {"lo": round(float(lo), 4), "median": round(float(med), 4), "hi": round(float(hi), 4), "n_boot": n_boot, "n_patients": int(len(ids))}
