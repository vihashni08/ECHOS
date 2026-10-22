"""Vitals episode tracker: a two-state hidden Markov model per patient (S = stable, U = unstable episode).

Why a hidden state: a validated BP / pulse alert (label) is a noisy sign of an unstable period - an episode can continue on days with no
label, and a single label can be a one-off. The tracker keeps a belief pi_t = P(U at day t | everything up to day t).

Model (parameters fitted on TRAINING patients only):
    transitions   a = P(S -> U),  b = P(U -> U)              (Baum-Welch / EM on the label sequences)
    label         theta_s = P(label | S),  theta_u = P(label | U)
    readings      L_t = [q_t / (1 - q_t)] / [rho / (1 - rho)] (scaled likelihood ratio; q_t = calibrated logistic regression on the
                  day's personal features, trained against the EM posterior of U; rho = its base rate). No reading -> L_t = 1.

Forward filter for day t:
    predict   pi-_t = pi_{t-1} * b + (1 - pi_{t-1}) * a_i
    update    pi_t  = pi-_t * l_U / (pi-_t * l_U + (1 - pi-_t) * l_S),   l_U = P(label_t | U) * L_t,  l_S = P(label_t | S)

Personal transitions: a_i = (m * a + n_on) / (m + n_stable), a Beta posterior that moves the population onset rate towards the patient's own
past rate (n_on = past label onsets, n_stable = past label-free days; past only).

Forecast made at the end of day t: P(any label on t+1..t+H) = 1 - P(no label on t+1..t+H), computed exactly by propagating the 2-state chain:
    alpha_0 = [1 - pi_t, pi_t];  alpha_k = (alpha_{k-1} A) * [1 - theta_s, 1 - theta_u];  P(no label) = sum(alpha_H)."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

EPS = 1e-9


@dataclass
class HMMParams:
    a: float = 0.05        # P(S -> U)
    b: float = 0.70        # P(U -> U)
    theta_s: float = 0.02  # P(label | S)
    theta_u: float = 0.50  # P(label | U)
    pi0: float = 0.15      # P(U) on a patient's first day

    def A(self, a: float | None = None) -> np.ndarray:
        a = self.a if a is None else a
        return np.array([[1 - a, a], [1 - self.b, self.b]])

    def to_dict(self) -> dict:
        return {k: float(getattr(self, k)) for k in ("a", "b", "theta_s", "theta_u", "pi0")}


def _emis(label: np.ndarray, p: HMMParams) -> np.ndarray:
    """P(label_t | state) for state S, U -> shape (T, 2)."""
    th = np.array([p.theta_s, p.theta_u])
    return np.where(label[:, None] == 1, th[None, :], 1 - th[None, :])


def _forward_backward(label: np.ndarray, p: HMMParams):
    T = len(label)
    B = _emis(label, p)
    A = p.A()
    alpha = np.zeros((T, 2))
    c = np.zeros(T)
    alpha[0] = np.array([1 - p.pi0, p.pi0]) * B[0]
    c[0] = alpha[0].sum() + EPS
    alpha[0] /= c[0]
    for t in range(1, T):
        alpha[t] = (alpha[t - 1] @ A) * B[t]
        c[t] = alpha[t].sum() + EPS
        alpha[t] /= c[t]
    beta = np.ones((T, 2))
    for t in range(T - 2, -1, -1):
        beta[t] = (A @ (B[t + 1] * beta[t + 1])) / c[t + 1]
    gamma = alpha * beta
    gamma /= gamma.sum(1, keepdims=True) + EPS
    xi = np.zeros((2, 2))
    for t in range(T - 1):
        m = alpha[t][:, None] * A * (B[t + 1] * beta[t + 1])[None, :] / c[t + 1]
        xi += m / (m.sum() + EPS)
    return gamma, xi, float(np.log(c).sum())


def fit_hmm(sequences: list[np.ndarray], n_iter: int = 100, tol: float = 1e-5, init: HMMParams | None = None) -> tuple[HMMParams, list[float]]:
    """Baum-Welch on binary label sequences (one per training patient). Keeps state U as the one with the higher label rate."""
    p = init or HMMParams()
    ll_hist: list[float] = []
    for _ in range(n_iter):
        xi_sum = np.zeros((2, 2))
        g0 = np.zeros(2)
        num = np.zeros(2)
        den = np.zeros(2)
        ll = 0.0
        for lab in sequences:
            if len(lab) < 2:
                continue
            gamma, xi, l = _forward_backward(lab, p)
            ll += l
            xi_sum += xi
            g0 += gamma[0]
            num += (gamma * lab[:, None]).sum(0)
            den += gamma.sum(0)
        A = xi_sum / (xi_sum.sum(1, keepdims=True) + EPS)
        th = num / (den + EPS)
        p = HMMParams(a=float(A[0, 1]), b=float(A[1, 1]), theta_s=float(np.clip(th[0], 1e-4, 1 - 1e-4)),
                      theta_u=float(np.clip(th[1], 1e-4, 1 - 1e-4)), pi0=float(np.clip(g0[1] / (g0.sum() + EPS), 1e-3, 1 - 1e-3)))
        if p.theta_s > p.theta_u:  # label switching: keep U = the label-rich state
            p = HMMParams(a=1 - p.b, b=1 - p.a, theta_s=p.theta_u, theta_u=p.theta_s, pi0=1 - p.pi0)
        ll_hist.append(ll)
        if len(ll_hist) > 1 and abs(ll_hist[-1] - ll_hist[-2]) < tol * max(1.0, abs(ll_hist[-2])):
            break
    return p, ll_hist


def smoothed_state(sequences: list[np.ndarray], p: HMMParams) -> list[np.ndarray]:
    """EM posterior P(U_t | whole label sequence) - used ONLY on training patients, as the soft target of the readings model."""
    return [_forward_backward(lab, p)[0][:, 1] if len(lab) else np.array([]) for lab in sequences]


@dataclass
class ReadingsModel:
    """Discriminative emission: q_t = P(U | today's readings), turned into a scaled likelihood ratio."""
    features: list[str]
    model: object = None
    rho: float = 0.2

    def fit(self, X: pd.DataFrame, soft_u: np.ndarray, has_reading: np.ndarray) -> "ReadingsModel":
        m = has_reading.astype(bool)
        Xr = X.loc[m, self.features].fillna(0).values
        u = soft_u[m]
        Xd = np.vstack([Xr, Xr])
        yd = np.r_[np.ones(len(Xr)), np.zeros(len(Xr))]
        wd = np.r_[u, 1 - u] + 1e-6
        self.model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=4000, C=0.5))
        self.model.fit(Xd, yd, logisticregression__sample_weight=wd)
        self.rho = float(np.clip(u.mean(), 1e-3, 1 - 1e-3))
        return self

    def likelihood_ratio(self, X: pd.DataFrame, has_reading: np.ndarray) -> np.ndarray:
        q = np.clip(self.model.predict_proba(X[self.features].fillna(0).values)[:, 1], 1e-4, 1 - 1e-4)
        lr = (q / (1 - q)) / (self.rho / (1 - self.rho))
        return np.where(has_reading.astype(bool), lr, 1.0)

    def coefficients(self) -> dict:
        lr = self.model.named_steps["logisticregression"]
        return dict(zip(self.features, map(float, lr.coef_[0])))


@dataclass
class EpisodeTracker:
    """Online filter for ONE patient. `step` is called once per calendar day with that day's label (0/1, validated by the end of the day)
    and the readings likelihood ratio (1.0 if no reading). Everything it uses is in the past."""
    params: HMMParams
    personal_strength: float = 30.0   # m: how many label-free days of the patient's own history equal the population prior
    use_personal: bool = True
    pi: float | None = None
    n_stable: int = 0
    n_on: int = 0
    prev_label: int = 0
    history: list = field(default_factory=list)

    def onset_rate(self) -> float:
        if not self.use_personal:
            return self.params.a
        m = self.personal_strength
        return float((m * self.params.a + self.n_on) / (m + self.n_stable))

    def predict(self) -> float:
        if self.pi is None:
            return self.params.pi0
        a = self.onset_rate()
        return self.pi * self.params.b + (1 - self.pi) * a

    def step(self, label: int, lr: float = 1.0) -> float:
        prior = self.predict()
        p = self.params
        lu = (p.theta_u if label else 1 - p.theta_u) * lr
        ls = p.theta_s if label else 1 - p.theta_s
        self.pi = float(prior * lu / (prior * lu + (1 - prior) * ls + EPS))
        if not self.prev_label:      # personal onset counts (label-free day followed by a label = onset)
            self.n_stable += 1
            self.n_on += int(label)
        self.prev_label = int(label)
        self.history.append(self.pi)
        return self.pi

    def forecast(self, horizon: int = 3) -> float:
        """P(at least one validated label on the next `horizon` days)."""
        p = self.params
        alpha = np.array([1 - self.pi, self.pi]) if self.pi is not None else np.array([1 - p.pi0, p.pi0])
        A = p.A(self.onset_rate())
        stay = np.array([1 - p.theta_s, 1 - p.theta_u])
        for _ in range(horizon):
            alpha = (alpha @ A) * stay
        return float(1 - alpha.sum())


def run_tracker(labels: np.ndarray, lrs: np.ndarray, params: HMMParams, horizon: int = 3, use_personal: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """Filter a whole patient series; returns (belief pi_t, forecast_t) for every day."""
    tr = EpisodeTracker(params, use_personal=use_personal)
    pis, fc = np.zeros(len(labels)), np.zeros(len(labels))
    for t, (lab, lr) in enumerate(zip(labels, lrs)):
        pis[t] = tr.step(int(lab), float(lr))
        fc[t] = tr.forecast(horizon)
    return pis, fc
