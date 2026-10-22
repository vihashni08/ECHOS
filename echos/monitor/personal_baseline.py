"""Personal Bayesian baseline: what is usual for THIS person, and how unusual today's reading is.

Each patient's readings of one vital are modelled as x ~ Normal(mu, sigma^2) with a Normal-Inverse-Gamma prior
(mu, sigma^2) ~ NIG(mu0, kappa0, alpha0, beta0). The prior is fitted on TRAINING patients only (empirical Bayes, `fit_prior`), so a new
patient starts at the population usual and becomes personal as readings arrive. Conjugate update after n past readings with mean xbar:

    kappa_n = kappa0 + n
    mu_n    = (kappa0 * mu0 + n * xbar) / kappa_n
    alpha_n = alpha0 + n / 2
    beta_n  = beta0 + 0.5 * sum (x_i - xbar)^2 + kappa0 * n * (xbar - mu0)^2 / (2 * kappa_n)

Posterior predictive for the next reading: Student-t(df = 2 alpha_n, loc = mu_n, scale = sqrt(beta_n (kappa_n + 1) / (alpha_n kappa_n))).
Outputs per reading (computed from PAST readings only): z = (x - mu_n) / scale, surprise = -log p(x), and the personal usual (mu_n, scale)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats


@dataclass(frozen=True)
class NIGPrior:
    mu0: float
    kappa0: float
    alpha0: float
    beta0: float

    def to_dict(self) -> dict:
        return {"mu0": self.mu0, "kappa0": self.kappa0, "alpha0": self.alpha0, "beta0": self.beta0}


def fit_prior(values: pd.Series, groups: pd.Series, alpha0: float = 3.0, min_n: int = 3) -> NIGPrior:
    """Empirical-Bayes NIG prior by moment matching across training patients.

    mu0 = mean of patient means; E[sigma^2] = beta0 / (alpha0 - 1) = mean within-patient variance;
    kappa0 = within-patient variance / between-patient variance of means (how many readings the population mean is 'worth')."""
    df = pd.DataFrame({"x": values.values, "g": groups.values}).dropna()
    st = df.groupby("g").x.agg(["mean", "var", "count"])
    st = st[st["count"] >= min_n]
    if len(st) < 2:
        raise ValueError("fit_prior needs >= 2 patients with readings")
    within = float(st["var"].mean())
    between = float(max(st["mean"].var() - within / st["count"].mean(), 1e-6))
    kappa0 = float(np.clip(within / between, 0.5, 20.0))
    return NIGPrior(mu0=float(st["mean"].mean()), kappa0=kappa0, alpha0=alpha0, beta0=within * (alpha0 - 1.0))


def posterior(prior: NIGPrior, xs: np.ndarray) -> tuple[float, float, float, float]:
    xs = np.asarray(xs, dtype=float)
    xs = xs[~np.isnan(xs)]
    n = len(xs)
    if n == 0:
        return prior.mu0, prior.kappa0, prior.alpha0, prior.beta0
    xbar = float(xs.mean())
    ss = float(((xs - xbar) ** 2).sum())
    kn = prior.kappa0 + n
    mn = (prior.kappa0 * prior.mu0 + n * xbar) / kn
    an = prior.alpha0 + n / 2.0
    bn = prior.beta0 + 0.5 * ss + prior.kappa0 * n * (xbar - prior.mu0) ** 2 / (2.0 * kn)
    return mn, kn, an, bn


def predictive(prior: NIGPrior, past: np.ndarray) -> stats.rv_continuous:
    mn, kn, an, bn = posterior(prior, past)
    return stats.t(df=2.0 * an, loc=mn, scale=float(np.sqrt(bn * (kn + 1.0) / (an * kn))))


def personal_scores(x: np.ndarray, prior: NIGPrior, window: int = 28) -> dict[str, np.ndarray]:
    """For a patient's daily series x (NaN = no reading), score each reading against the predictive built from the previous `window`
    READINGS only (never the reading itself). Returns arrays usual, scale, z, surprise (NaN where there is no reading)."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    usual, scale, z, surprise = (np.full(n, np.nan) for _ in range(4))
    past: list[float] = []
    for t in range(n):
        pred = predictive(prior, np.array(past[-window:]))
        loc, sc = float(pred.mean()), float(pred.kwds["scale"]) if "scale" in pred.kwds else float(pred.std())
        usual[t], scale[t] = loc, sc
        if not np.isnan(x[t]):
            z[t] = (x[t] - loc) / sc
            surprise[t] = -float(pred.logpdf(x[t]))
            past.append(float(x[t]))
    return {"usual": usual, "scale": scale, "z": z, "surprise": surprise}


def add_personal_features(d: pd.DataFrame, priors: dict[str, NIGPrior], window: int = 28) -> pd.DataFrame:
    """Adds <v>_usual, <v>_scale, <v>_z, <v>_surprise for each vital in `priors` (per patient, past-only)."""
    d = d.sort_values(["patient_id", "day"]).copy()
    for v, pr in priors.items():
        cols = {k: np.full(len(d), np.nan) for k in ("usual", "scale", "z", "surprise")}
        for _, idx in d.groupby("patient_id").indices.items():
            sc = personal_scores(d[v].values[idx], pr, window)
            for k in cols:
                cols[k][idx] = sc[k]
        for k, arr in cols.items():
            d[f"{v}_{k}"] = arr
    return d
