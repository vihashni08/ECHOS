"""Calibration of the vitals and behaviour sub-models (isotonic vs Platt), out-of-fold Brier before/after.
Adopts calibration only if the Brier score improves; writes new model files (old ones kept) and reports/metrics/calibration.json.
Usage: PYTHONPATH=. python scripts/calibrate.py"""
import json

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss

from echos.agents.health import behaviour as b
from echos.agents.health import calibration as C
from echos.agents.health import vitals as v
from echos.config import ROOT, load_config
from echos.data.guard import assert_real_source
from echos.data.patient_day import load_patient_day

cfg = load_config()
seed, nf = cfg["seed"], cfg["splits"]["n_folds"]
pdy = load_patient_day()
out = {"notes": ["Brier on out-of-fold scores; calibrator for each outer fold is fitted on an inner out-of-fold of that fold's TRAINING patients only",
                 "raw scores come from models trained with scale_pos_weight, so they are inflated relative to true probabilities",
                 "final calibrator is fitted on the plain 5-fold OOF raw scores and applied to the final (all-data) model"]}
for name, mod, feats, path_old, new_name in [("vitals", v, v.FEATURES, v.MODEL_PATH, "physiological_calibrated.joblib"),
                                             ("behaviour", b, b.CLF_FEATURES, b.MODEL_PATH, "behaviour_calibrated.joblib")]:
    if name == "vitals":
        df = v.build_features(pdy)
    else:
        df = b.build_features(pdy)
        df = df[df.has_activity & df.total_events_delta.notna() & df.total_events_z.notna()].reset_index(drop=True)
    assert_real_source(df, "tihm")
    y = df.y.values
    raw, cal = C.nested_oof(df, feats, mod._model, seed, nf)
    res = {"n": int(len(df)), "prevalence": float(y.mean()), "brier_baseline_prevalence": float(brier_score_loss(y, np.full(len(y), y.mean()))),
           "brier_raw": float(brier_score_loss(y, raw)), "brier": {k: float(brier_score_loss(y, c)) for k, c in cal.items()},
           "curve_raw": C.curve_points(y, raw), "curve": {k: C.curve_points(y, c) for k, c in cal.items()}}
    best = min(cal, key=lambda k: res["brier"][k])
    res["chosen_kind"] = best
    res["adopted"] = bool(res["brier"][best] < res["brier_raw"])
    if res["adopted"]:
        old = joblib.load(path_old)
        calib = C.Calibrator(best).fit(raw, y)          # fitted on plain OOF raw scores
        art = {**old, "calibrator": calib, "calibration_kind": best}
        art["threshold_raw"] = old["threshold"]
        art["threshold"] = float(calib(np.array([old["threshold"]]))[0])
        joblib.dump(art, ROOT / "models" / new_name)
        res["model_file"] = f"models/{new_name}"
        res["threshold_raw_to_calibrated"] = [old["threshold"], art["threshold"]]
    out[name] = res
    print(f"{name}: Brier raw {res['brier_raw']:.4f} -> isotonic {res['brier']['isotonic']:.4f} / platt {res['brier']['platt']:.4f} "
          f"(prevalence-only {res['brier_baseline_prevalence']:.4f}); adopted={res['adopted']} ({best})")
out["use_calibrated"] = bool(all(out[k]["adopted"] for k in ("vitals", "behaviour")))
(ROOT / "reports/metrics/calibration.json").write_text(json.dumps(out, indent=2))
