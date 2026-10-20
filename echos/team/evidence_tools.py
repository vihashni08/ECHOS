"""Computation behind the Case Investigator's tools. Pure functions on a patient's PAST data (day < today; no labels from today or later), so the
agent and scripts/evaluation/investigator_eval.py use the same code. Nothing here is a learned model: robust z-scores against the person's own past, a short
trend check and a lookup of the person's own earlier deviations. Not a diagnosis and not a probability."""
from __future__ import annotations

import numpy as np
import pandas as pd

VITALS = ("sys", "dia", "hr")
MIN_SCALE = {"sys": 3.0, "dia": 2.0, "hr": 2.0}     # same floor as the vitals detector (echos/agents/health/vitals.py)
Z_FLAG = 2.0                                         # |z| at or beyond this = outside the personal usual
WINDOW = 28
CLINICAL_LABELS = ("bp_alert", "pulse_alert", "water_alert")


def _scale(past: np.ndarray, var: str) -> tuple[float, float]:
    med = float(np.median(past))
    mad = float(np.median(np.abs(past - med)))
    return med, max(1.4826 * mad, MIN_SCALE[var])


def robust_z(past, current: float, var: str, min_n: int = 3):
    """(z, baseline) of `current` against the median / MAD of `past` (the last WINDOW readings), or (None, None) with fewer than `min_n` readings."""
    p = np.asarray([x for x in list(past)[-WINDOW:] if x is not None and not pd.isna(x)], dtype=float)
    if len(p) < min_n or current is None or pd.isna(current):
        return None, None
    med, sc = _scale(p, var)
    return (float(current) - med) / sc, med


def trend_check(past, current: float, var: str) -> dict:
    """Does today's deviation sit on a trend? past = earlier readings (oldest first). Uses the last 5 readings including today.
    confirmed = |z| >= Z_FLAG and the last three readings (incl. today) move in the direction of the deviation (non-decreasing away from baseline)."""
    z, base = robust_z(past, current, var)
    if z is None:
        return {"available": False}
    series = [float(x) for x in list(past)[-4:] if x is not None and not pd.isna(x)] + [float(current)]
    slope = float(np.polyfit(np.arange(len(series)), series, 1)[0]) if len(series) >= 3 else 0.0
    sign = 1 if z >= 0 else -1
    last3 = series[-3:]
    moving = len(last3) == 3 and all((b - a) * sign >= 0 for a, b in zip(last3, last3[1:]))
    return {"available": True, "z": round(z, 2), "baseline": round(base, 1), "current": round(float(current), 1),
            "slope_per_reading": round(slope, 2), "moving_away": bool(moving), "outside_usual": bool(abs(z) >= Z_FLAG),
            "confirmed": bool(abs(z) >= Z_FLAG and moving and slope * sign > 0)}


def personal_history(pdy: pd.DataFrame, patient_id: str, day: str) -> dict:
    """The person's OWN earlier days (day < today): how often they were outside their usual on any vital (past-only z at that time) and how many of
    those days carried a recorded clinical event (blood pressure / pulse / body water label). Labels of today or later are never read."""
    g = pdy[(pdy.patient_id == patient_id) & (pdy.day < pd.Timestamp(day).normalize())].sort_values("day")
    rd = g[g.has_reading]
    dev_days = []
    for i in range(len(rd)):
        row = rd.iloc[i]
        zs = [z for z in (robust_z(rd[v].iloc[:i].tolist(), row[v], v)[0] for v in VITALS) if z is not None]
        if zs and max(abs(z) for z in zs) >= Z_FLAG:
            dev_days.append(row)
    n_dev = len(dev_days)
    n_event = sum(1 for r in dev_days if any(bool(r.get(c)) for c in CLINICAL_LABELS if c in r.index))
    n_events_all = int(sum(bool(r[c]) for _, r in g.iterrows() for c in CLINICAL_LABELS if c in g.columns))
    return {"prior_days": int(len(g)), "prior_reading_days": int(len(rd)), "prior_deviation_days": n_dev,
            "prior_deviation_days_with_event": n_event, "prior_event_labels": n_events_all}
