"""Train / apply the vitals part of ECHOS v2: personal NIG baselines -> readings likelihood ratio -> episode tracker (HMM).

`fit_vitals_model(train)` uses training patients only. `score(df)` runs the online filter over each patient's calendar and returns, for every
day: personal usual / z / surprise per vital, the readings likelihood ratio, the belief pi_t = P(unstable) and the H-day forecast."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from echos.data.guard import assert_real_source
from echos.monitor import episode as E
from echos.monitor import personal_baseline as PB

VITALS = ("sys", "dia", "hr")


def readings_features() -> list[str]:
    f = []
    for v in VITALS:
        f += [v, f"{v}_z", f"{v}_surprise"]
    return f + ["sys_abs_z", "dia_abs_z", "hr_abs_z"]


def _add_derived(d: pd.DataFrame) -> pd.DataFrame:
    for v in VITALS:
        d[f"{v}_abs_z"] = d[f"{v}_z"].abs()
    return d


@dataclass
class VitalsModel:
    priors: dict
    hmm: E.HMMParams
    readings: E.ReadingsModel | None
    horizon: int = 3
    window: int = 28
    use_personal: bool = True
    info: dict = field(default_factory=dict)

    def features(self, d: pd.DataFrame) -> pd.DataFrame:
        return _add_derived(PB.add_personal_features(d, self.priors, self.window))

    def score(self, d: pd.DataFrame, features_done: bool = False) -> pd.DataFrame:
        d = d if features_done else self.features(d)
        d = d.sort_values(["patient_id", "day"]).copy()
        has = d["has_reading"].values
        d["lr"] = self.readings.likelihood_ratio(d, has) if self.readings is not None else 1.0
        d["belief"] = np.nan
        d["forecast"] = np.nan
        for _, idx in d.groupby("patient_id").indices.items():
            pis, fc = E.run_tracker(d["event"].values[idx], d["lr"].values[idx], self.hmm, self.horizon, self.use_personal)
            d.iloc[idx, d.columns.get_loc("belief")] = pis
            d.iloc[idx, d.columns.get_loc("forecast")] = fc
        return d


def fit_vitals_model(train: pd.DataFrame, horizon: int = 3, window: int = 28, use_readings: bool = True,
                     use_personal: bool = True) -> tuple[VitalsModel, pd.DataFrame]:
    """Returns the fitted model and the training frame with personal features (handy for the readings model and for reports)."""
    assert_real_source(train, "tihm")
    priors = {v: PB.fit_prior(train[v], train["patient_id"]) for v in VITALS}
    tr = _add_derived(PB.add_personal_features(train, priors, window)).sort_values(["patient_id", "day"])
    seqs = [g["event"].values.astype(int) for _, g in tr.groupby("patient_id")]
    hmm, ll = E.fit_hmm(seqs)
    readings = None
    if use_readings:
        soft = np.concatenate(E.smoothed_state(seqs, hmm))
        readings = E.ReadingsModel(readings_features()).fit(tr, soft, tr["has_reading"].values)
    info = {"hmm": hmm.to_dict(), "em_iterations": len(ll), "priors": {v: p.to_dict() for v, p in priors.items()}}
    if readings is not None:
        info["readings_coef"] = readings.coefficients()
        info["readings_rho"] = readings.rho
    return VitalsModel(priors, hmm, readings, horizon, window, use_personal, info), tr
