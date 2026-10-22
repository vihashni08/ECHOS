"""Review allocation used by the Review Coordinator: who gets today's K clinician reviews.

Priority of patient i at the end of day t puts the two detectors on the same scale by their LIFT over their own base rate:
    r_i = max( v_i / v_bar , a_i / a_bar )
    v_i = P(validated BP / pulse event in the next 3 days)       (personal Bayesian rate, echos/monitor/risk.py); v_bar = its population mean
    a_i = max calibrated P(agitation) over today's three windows  (echos/monitor/agitation.py);  a_bar = its population mean
(A plain probability combination 1 - (1 - v)(1 - a) was tested first and let the larger vitals probabilities crowd out agitation:
scripts/evaluation/allocation_eval.py keeps it as the 'prob_union' policy.)
Fairness floor: a patient not reviewed for >= M days is due; at most floor(K * fair_share) slots go to due patients (highest r first), the rest
to the highest r. URGENT safety cases are not part of the budget - they go straight to the Care agent."""
from __future__ import annotations

from dataclasses import dataclass


def _num(x) -> float:
    return 0.0 if x is None or x != x else float(x)


def priority(v: float | None, a: float | None, v_bar: float, a_bar: float) -> float:
    """Lift-based priority (see module docstring). Missing signal = 0."""
    return max(_num(v) / max(v_bar, 1e-9), _num(a) / max(a_bar, 1e-9))


def prob_union(v: float | None, a: float | None) -> float:
    return 1.0 - (1.0 - _num(v)) * (1.0 - _num(a))


@dataclass(frozen=True)
class Candidate:
    patient_id: str
    priority: float
    days_since_review: int
    vitals_risk: float | None = None
    agitation_risk: float | None = None


def select(cands: list[Candidate], k: int, fairness_days: int | None = 7, fair_share: float = 0.5) -> list[tuple[Candidate, str]]:
    """Returns up to k (candidate, reason) pairs; reason is 'priority' or 'fairness'. Deterministic (ties broken by patient id)."""
    order = sorted(cands, key=lambda c: (-c.priority, c.patient_id))
    picked: list[tuple[Candidate, str]] = []
    if fairness_days:
        due = [c for c in order if c.days_since_review >= fairness_days]
        for c in due[: int(k * fair_share)]:
            picked.append((c, "fairness"))
    chosen = {c.patient_id for c, _ in picked}
    for c in order:
        if len(picked) >= k:
            break
        if c.patient_id not in chosen:
            picked.append((c, "priority"))
            chosen.add(c.patient_id)
    return picked
