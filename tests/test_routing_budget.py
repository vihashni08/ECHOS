"""Call-budget choice of the routing cutoff theta (echos/team/routing_budget.py): quantile / budget logic, ties, zero budget, patient bootstrap."""

import numpy as np
import pytest

from echos.team import routing_budget as RB


def test_allowed_calls_uses_all_patient_days_over_seven():
    assert RB.allowed_calls(0.25, 700) == 25 and RB.allowed_calls(1.0, 1266) == 180 and RB.allowed_calls(0.1, 20) == 0


def test_theta_is_the_smallest_value_keeping_false_calls_within_the_budget():
    p = np.arange(1, 101) / 100.0                       # 100 Normal days, P = 0.01 .. 1.00
    n_days = 700                                         # 100 patient-weeks; B = 0.1 -> 10 calls
    theta = RB.budget_theta(p, 0.1, n_days)
    assert theta == pytest.approx(0.90) and RB.false_calls(p, theta) == 10
    assert RB.false_calls(p, theta - 0.0101) > 10           # any smaller theta (past the next value) breaks the budget
    assert RB.budget_theta(p, 0.5, n_days) == pytest.approx(0.50)      # monotone: bigger budget, lower theta


def test_ties_at_the_threshold_are_not_called():
    p = np.array([0.9, 0.5, 0.5, 0.5, 0.5, 0.1])        # budget allows 3 calls, but four days tie at 0.5
    theta = RB.budget_theta(p, 3.0, 7)                  # 1 patient-week -> 3 calls
    assert theta == pytest.approx(0.5) and RB.false_calls(p, theta) == 1                 # strict '>': fewer calls than allowed, never more


def test_zero_budget_calls_nothing_and_a_budget_above_every_day_calls_all():
    p = np.array([0.2, 0.7, 0.4])
    theta0 = RB.budget_theta(p, 0.0, 700)
    assert theta0 == pytest.approx(0.7) and RB.false_calls(p, theta0) == 0
    assert RB.budget_theta(p, 100.0, 7) == 0.0 and RB.false_calls(p, 0.0) == 3
    assert RB.budget_theta([], 0.25, 100) == 0.0


def test_rounding_up_keeps_the_budget():
    p = np.array([0.31281, 0.1, 0.05])
    theta = RB.budget_theta(p, 0.0, 7)
    assert theta >= 0.31281 and RB.false_calls(p, theta) == 0


def test_bootstrap_resamples_patients_and_is_deterministic():
    rng = np.random.default_rng(0)
    patient = np.repeat(np.array(list("abcdefghij")), 70)
    p = rng.random(len(patient)) * 0.5
    normal = np.ones(len(patient), bool)
    a = RB.bootstrap_theta(p, patient, normal, 0.25, 200, 7)
    assert a == RB.bootstrap_theta(p, patient, normal, 0.25, 200, 7)
    assert a["n_patients"] == 10 and a["lo"] <= a["median"] <= a["hi"] <= 0.5
