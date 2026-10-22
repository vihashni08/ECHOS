"""Train and save the v2 models used by the runtime -> models/v2/ (git-ignored).

For each of the 5 patient folds (the same splits as the evaluation scripts) and for 'all' patients:
  NIG priors (sys, dia, hr) and the Beta event-rate prior  - fitted on the vitals cohort's training patients
  the agitation model (XGBoost + Platt)                      - fitted on the agitation table's training patients
plus the fold of every patient and the population base rates the Review Coordinator divides by (from the out-of-fold scores).

    PYTHONPATH=. python scripts/train_v2.py
"""
from __future__ import annotations

import json
import time

import joblib
import numpy as np
import pandas as pd

from echos.config import ROOT, load_config
from echos.data.splits import patient_folds
from echos.monitor import agitation as A, cohort, personal_baseline as PB, risk as R
from echos.monitor.store import DIR, clear_cache
from echos.monitor.vitals_model import VITALS


def fold_map(df: pd.DataFrame, folds) -> dict:
    out = {}
    for f, (_, te) in enumerate(folds):
        for pid in df.iloc[te].patient_id.unique():
            out[str(pid)] = f
    return out


def main() -> dict:
    t0 = time.time()
    cfg = load_config()
    DIR.mkdir(parents=True, exist_ok=True)
    vit = cohort.vitals_cohort()
    ag = A.add_slot_dummies(A.build_table())
    vf = list(patient_folds(vit, cfg["splits"]["n_folds"], cfg["seed"]))
    af = list(patient_folds(ag, cfg["splits"]["n_folds"], cfg["seed"]))
    feats = A.feature_sets()["full"]
    meta = {"vitals_fold_of": fold_map(vit, vf), "agitation_fold_of": fold_map(ag, af), "folds": {}, "agitation_features": feats}
    parts = [(str(f), vit.iloc[vtr], ag.iloc[atr]) for f, ((vtr, _), (atr, _)) in enumerate(zip(vf, af))] + [("all", vit, ag)]
    for name, vtrain, atrain in parts:
        nig = {v: PB.fit_prior(vtrain[v], vtrain["patient_id"]).to_dict() for v in VITALS}
        beta = R.fit_from_frame(vtrain)
        model = A.AgitationModel(feats).fit(atrain)
        joblib.dump(model, DIR / f"agitation_{name}.joblib")
        meta["folds"][name] = {"nig": nig, "beta": {"a0": beta.a0, "b0": beta.b0}, "train_patients_vitals": int(vtrain.patient_id.nunique()),
                               "train_patients_agitation": int(atrain.patient_id.nunique())}
        print(f"fold {name}: vitals {meta['folds'][name]['train_patients_vitals']} patients, agitation {meta['folds'][name]['train_patients_agitation']}")
    oofv, oofa = ROOT / "data/v2/oof_vitals.parquet", ROOT / "data/v2/oof_agitation.parquet"
    if oofv.exists() and oofa.exists():
        v = pd.read_parquet(oofv)
        a = pd.read_parquet(oofa)
        a["day"] = (a["w"] - pd.Timedelta(hours=1)).dt.normalize()
        meta["base_rates"] = {"v_bar": float(v["beta_forecast"].mean()), "a_bar": float(a.groupby(["patient_id", "day"]).p_agitation.max().mean()),
                              "source": "out-of-fold scores (scripts/evaluation/*_eval.py)"}
    else:
        rate = R.fit_from_frame(vit).mean
        meta["base_rates"] = {"v_bar": float(R.forecast(rate, cfg["monitor"]["horizon_days"])), "a_bar": float(np.clip(ag.y.mean() * 3, 1e-3, 1)),
                              "source": "training prevalence (run the evaluation scripts first for out-of-fold base rates)"}
    meta["built_ts"] = time.time()
    (DIR / "meta.json").write_text(json.dumps(meta, indent=1))
    clear_cache()
    print(f"base rates {meta['base_rates']}  ({time.time() - t0:.0f} s) -> {DIR}")
    return meta


if __name__ == "__main__":
    main()
