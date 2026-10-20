"""Probability calibration for the vitals model (isotonic or Platt), fitted on training folds only.

nested_oof(): outer patient-level folds as in evaluate(); inside each outer training set an inner patient-level OOF of the raw
score fits the calibrator, so the calibrated out-of-fold score never sees its own fold or its labels."""
from __future__ import annotations

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

from echos.data.splits import patient_folds


class Calibrator:
    def __init__(self, kind: str):
        assert kind in ("isotonic", "platt")
        self.kind, self.m = kind, None

    @staticmethod
    def _logit(p):
        p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
        return np.log(p / (1 - p)).reshape(-1, 1)

    def fit(self, raw, y) -> "Calibrator":
        if self.kind == "isotonic":
            self.m = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(raw, y)
        else:
            self.m = LogisticRegression(C=1e6, max_iter=1000).fit(self._logit(raw), y)
        return self

    def __call__(self, raw):
        raw = np.asarray(raw, float)
        return self.m.predict(raw) if self.kind == "isotonic" else self.m.predict_proba(self._logit(raw))[:, 1]


def raw_oof(df, feats, make_model, y, seed, nf):
    o = np.zeros(len(df))
    for tr, te in patient_folds(df, nf, seed):
        m = make_model(seed, (1 - y[tr].mean()) / max(y[tr].mean(), 1e-6))
        o[te] = m.fit(df.iloc[tr][feats], y[tr]).predict_proba(df.iloc[te][feats])[:, 1]
    return o


def nested_oof(df, feats, make_model, seed, nf, inner_nf=4):
    """Returns (raw_oof, {kind: calibrated_oof}). df must have patient_id and y."""
    y = df.y.values
    raw, cal = np.zeros(len(df)), {k: np.zeros(len(df)) for k in ("isotonic", "platt")}
    for tr, te in patient_folds(df, nf, seed):
        dtr = df.iloc[tr].reset_index(drop=True)
        ytr = y[tr]
        inner = raw_oof(dtr, feats, make_model, ytr, seed, inner_nf)
        m = make_model(seed, (1 - ytr.mean()) / max(ytr.mean(), 1e-6)).fit(df.iloc[tr][feats], ytr)
        r = m.predict_proba(df.iloc[te][feats])[:, 1]
        raw[te] = r
        for k in cal:
            cal[k][te] = Calibrator(k).fit(inner, ytr)(r)
    return raw, cal


def curve_points(y, p, bins=10):
    """Quantile-bin reliability points: [{mean_pred, observed, n}]."""
    order = np.argsort(p, kind="stable")
    return [{"mean_pred": float(p[i].mean()), "observed": float(y[i].mean()), "n": int(len(i))}
            for i in np.array_split(order, bins) if len(i)]
