"""The equations behind triage (echos/orchestrator/triage_theory.py) and the properties of the real triage() rules."""
import math

import numpy as np
import pytest

from echos.orchestrator import triage_theory as TT


def test_bayes_threshold_and_implied_cost_ratio_round_trip():
    assert TT.bayes_threshold(1, 1) == 0.5 and TT.bayes_threshold(1, 9) == pytest.approx(0.1)
    for ratio in (0.25, 1.0, 2.6, 9.0):
        assert TT.implied_cost_ratio(TT.bayes_threshold(1.0, ratio)) == pytest.approx(ratio)
    assert TT.implied_cost_ratio(0.0) == math.inf and TT.implied_cost_ratio(1.0) == 0.0


@pytest.mark.parametrize("c_miss", [1.0, 3.0, 9.0])
def test_for_calibrated_scores_the_cost_minimising_threshold_is_c_fa_over_c_fa_plus_c_miss(c_miss):
    rng = np.random.default_rng(0)
    p = rng.random(200_000)
    y = (rng.random(200_000) < p).astype(int)                     # calibrated by construction: P(y = 1 | p) = p
    grid = np.linspace(0.02, 0.98, 97)
    best = grid[int(np.argmin([TT.expected_cost(p, y, t, 1.0, c_miss) for t in grid]))]
    assert best == pytest.approx(TT.bayes_threshold(1.0, c_miss), abs=0.03)


def test_union_rate_and_brier_skill():
    assert TT.union_rate([0.0, 0.0]) == 0.0 and TT.union_rate([0.5, 0.5]) == pytest.approx(0.75) and TT.union_rate([1.0, 0.2]) == 1.0
    assert TT.brier_skill(0.0889, 0.1487) == pytest.approx(0.402, abs=1e-3) and TT.brier_skill(0.1, 0.1) == 0.0


def test_triage_properties_hold_on_the_whole_grid():
    r = TT.check_properties()
    assert r["grid_points"] > 1000
    assert {k: v for k, v in r.items() if k.startswith("violations_")} == {"violations_monotone": 0, "violations_gap_is_normal": 0,
                                                                          "violations_critical_not_urgent": 0, "violations_missing_output_is_normal": 0}
