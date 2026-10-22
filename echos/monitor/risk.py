"""Personal Bayesian event rate (Beta-Bernoulli with an empirical-Bayes prior): the vitals review-priority model of ECHOS v2.

Each patient i has an unknown daily rate r_i of validated BP / pulse events, r_i ~ Beta(a0, b0). The prior is fitted on TRAINING patients by
moment matching (method of moments for a beta-binomial):

    mu  = sum k_j / sum n_j                       (pooled rate)
    v   = Var_j(k_j / n_j) - mean_j(mu (1 - mu) / n_j)   (between-patient variance beyond binomial noise)
    s   = mu (1 - mu) / v - 1                      (prior strength, in pseudo-days)
    a0  = mu * s,  b0 = (1 - mu) * s

Online update at the end of day t with k_t events in n_t observed days (past only):  r_hat_t = (a0 + k_t) / (a0 + b0 + n_t).
A new patient starts at the pooled rate and moves to their own rate as days accumulate (shrinkage), which is what helps in the first weeks.
Forecast for the next H days: P(any event) = 1 - (1 - r_hat_t)^H. 95% credible interval from Beta(a0 + k_t, b0 + n_t - k_t)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats


@dataclass(frozen=True)
class BetaPrior:
    a0: float
    b0: float

    @property
    def mean(self) -> float:
        return self.a0 / (self.a0 + self.b0)

    @property
    def strength(self) -> float:
        return self.a0 + self.b0

    def to_dict(self) -> dict:
        return {"a0": self.a0, "b0": self.b0, "pooled_rate": self.mean, "strength_days": self.strength}


def fit_beta_prior(k: np.ndarray, n: np.ndarray, min_strength: float = 1.0, max_strength: float = 500.0) -> BetaPrior:
    k, n = np.asarray(k, float), np.asarray(n, float)
    m = n > 0
    k, n = k[m], n[m]
    mu = float(np.clip(k.sum() / n.sum(), 1e-4, 1 - 1e-4))
    v = float(np.var(k / n) - np.mean(mu * (1 - mu) / n))
    s = float(np.clip(mu * (1 - mu) / max(v, 1e-6) - 1, min_strength, max_strength))
    return BetaPrior(mu * s, (1 - mu) * s)


def fit_from_frame(train: pd.DataFrame, col: str = "event") -> BetaPrior:
    st = train.groupby("patient_id")[col].agg(["sum", "count"])
    return fit_beta_prior(st["sum"].values, st["count"].values)


def personal_rate(events: np.ndarray, prior: BetaPrior) -> np.ndarray:
    """Posterior mean rate at the END of each day (includes that day's event, which is validated by then)."""
    e = np.asarray(events, float)
    k = np.cumsum(e)
    n = np.arange(1, len(e) + 1)
    return (prior.a0 + k) / (prior.a0 + prior.b0 + n)


def forecast(rate: np.ndarray | float, horizon: int = 3):
    return 1 - (1 - np.asarray(rate)) ** horizon


def credible_interval(k: int, n: int, prior: BetaPrior, level: float = 0.95) -> tuple[float, float]:
    d = stats.beta(prior.a0 + k, prior.b0 + n - k)
    return float(d.ppf((1 - level) / 2)), float(d.ppf(1 - (1 - level) / 2))


@dataclass
class PersonalRisk:
    """Online state of one patient (used by the Patient Monitor agent)."""
    prior: BetaPrior
    k: int = 0
    n: int = 0

    def update(self, event: int) -> float:
        self.k += int(event)
        self.n += 1
        return self.rate

    @property
    def rate(self) -> float:
        return (self.prior.a0 + self.k) / (self.prior.a0 + self.prior.b0 + self.n)

    def forecast(self, horizon: int = 3) -> float:
        return float(forecast(self.rate, horizon))

    def interval(self) -> tuple[float, float]:
        return credible_interval(self.k, self.n, self.prior)
