"""The decision theory behind Triage stage 1 (echos/orchestrator/triage.py): small pure functions for the equations in docs/TRIAGE_ALGORITHM.md, and a property
check that sweeps the real `triage()` over a grid of inputs. Nothing here is used at run time; it exists so the justification can be tested.

  E1  severity       s(x) = max( s_urgent(x), s_monitor(x), s_gap(x) )   over  NORMAL < MONITOR < URGENT   (monotone: more evidence never lowers it)
  E2  threshold      alert iff  P(event | x) >= tau = c_fa / (c_fa + c_miss)      <=>  c_miss / c_fa = (1 - tau) / tau        (needs calibrated scores)
  E3  budget         tau_k = min { t : FA_k(t) <= B_k }   (Neyman-Pearson: highest recall at a fixed false-alert rate; the test is a threshold on the score)
  E4  alert load     a_day = 1 - prod_k (1 - a_k)  for independent detectors;  alerts per patient-week <= sum_k B_k  (union bound)
"""
from __future__ import annotations

import itertools
import math
from typing import Sequence

import numpy as np

RANK = {"NORMAL": 0, "MONITOR": 1, "URGENT": 2}


def bayes_threshold(c_fa: float, c_miss: float) -> float:
    """E2: the probability above which alerting has the lower expected cost."""
    return c_fa / (c_fa + c_miss)


def implied_cost_ratio(tau: float) -> float:
    """E2 inverted: how many false alerts one missed event is worth when tau is the threshold. tau <= 0 -> inf."""
    return math.inf if tau <= 0 else (1.0 - tau) / tau


def expected_cost(p: Sequence[float], y: Sequence[int], tau: float, c_fa: float = 1.0, c_miss: float = 1.0) -> float:
    """Mean cost per day of alerting when p >= tau: c_fa per false alert, c_miss per missed event."""
    p, y = np.asarray(p, float), np.asarray(y, int)
    alert = p >= tau
    return float((c_fa * (alert & (y == 0)).sum() + c_miss * (~alert & (y == 1)).sum()) / max(len(p), 1))


def brier_skill(brier: float, brier_base: float) -> float:
    """1 - Brier / Brier(prevalence): 0 = no better than always predicting the base rate; the closer to 1, the more the score can be read as a probability."""
    return 1.0 - brier / brier_base


def union_rate(rates: Sequence[float]) -> float:
    """E4: share of days with at least one alert if the detectors raise alerts independently."""
    return 1.0 - float(np.prod([1.0 - a for a in rates]))


def _results(vit=0.0, fall=0.0, inactive=False, gap=False, critical=False, vit_available=True):
    cur = {"sys": {"current": 200.0 if critical else 120.0}, "dia": {"current": 80.0}, "hr": {"current": 70.0}}
    return {"quality": {"evidence": {"quality_checked": True, "sensor_gap": gap, "days_since_last_reading": 0}},
            "health": {"degraded": False, "vitals": {"available": vit_available, "probability": vit, "current_vs_baseline": cur}},
            "fall": {"score": fall, "evidence": {"post_fall_inactive": inactive}}}


def check_properties(cfg: dict | None = None) -> dict:
    """Sweep the real triage() over a grid. Returns counts; every `violations_*` must be 0.
    monotone: raising one input (score up, flag on) never lowers the status. gap: a data gap is never NORMAL. critical: a critical vital is URGENT whatever the
    model score. missing: a missing detector output is never NORMAL."""
    from echos.config import load_config
    from echos.orchestrator.triage import triage
    cfg = cfg or load_config()["triage"]            # loaded once: triage() re-reads the config file on every call otherwise
    levels = (0.0, 0.1, 0.2, 0.3, 0.5, 0.79, 0.8, 0.95)
    flags = (False, True)
    rank = lambda **k: RANK[triage(_results(**k), 0, cfg).status.value]       # noqa: E731
    mono = grid = 0
    for v, f, ina, gap, crit in itertools.product(levels, levels, flags, flags, flags):
        base = dict(vit=v, fall=f, inactive=ina, gap=gap, critical=crit)
        r0 = rank(**base)
        grid += 1
        for k in ("vit", "fall"):
            nxt = levels[min(levels.index(base[k]) + 1, len(levels) - 1)]
            mono += rank(**{**base, k: nxt}) < r0
        for k in ("inactive", "gap", "critical"):
            mono += rank(**{**base, k: True}) < r0
    gap_v = sum(rank(vit=v, fall=f, gap=True) < RANK["MONITOR"] for v, f in itertools.product(levels, levels))
    crit_v = sum(rank(vit=v, critical=True) != RANK["URGENT"] for v in levels)
    miss = {"no_quality": {**_results(), "quality": None}, "no_health": {**_results(), "health": None}}
    miss_v = sum(RANK[triage(r, 0, cfg).status.value] == 0 for r in miss.values())
    return {"grid_points": grid, "violations_monotone": int(mono), "violations_gap_is_normal": int(gap_v), "violations_critical_not_urgent": int(crit_v),
            "violations_missing_output_is_normal": int(miss_v)}
