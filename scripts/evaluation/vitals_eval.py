"""Evaluate the vitals episode tracker against baselines (5-fold, split by PATIENT) -> reports/metrics/v2_vitals.json.

Target: a validated BP / pulse label on days t+1..t+3, scored with data up to the end of day t (no same-day re-detection).
Every score is out-of-fold: priors, EM parameters and the readings model are fitted on the training patients of each fold.

    PYTHONPATH=. python scripts/evaluation/vitals_eval.py [--boot 1000]
"""
from __future__ import annotations

import argparse
import json
import time

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from echos.config import ROOT, load_config
from echos.data.splits import patient_folds
from echos.monitor import cohort, metrics as M, risk as R
from echos.monitor.vitals_model import fit_vitals_model, readings_features

OUT = ROOT / "reports" / "metrics" / "v2_vitals.json"
OOF = ROOT / "data" / "v2" / "oof_vitals.parquet"


def history_features(d: pd.DataFrame) -> pd.DataFrame:
    g = d.groupby("patient_id")["event"]
    d["label_today"] = d["event"]
    d["labels_7d"] = g.transform(lambda s: s.rolling(7, min_periods=1).sum())
    d["label_rate"] = g.transform(lambda s: s.expanding().mean())
    return d


def main(n_boot: int = 1000) -> dict:
    t0 = time.time()
    cfg = load_config()["monitor"]
    H = cfg["horizon_days"]
    base = cohort.vitals_cohort()
    folds = list(patient_folds(base, load_config()["splits"]["n_folds"], load_config()["seed"]))
    variants = {"full": dict(), "no_readings": dict(use_readings=False), "no_personal_onset": dict(use_personal=False),
                "labels_only_population": dict(use_readings=False, use_personal=False)}
    scored = {k: [] for k in variants}
    xgb_parts, fold_info, rate_parts = [], [], []
    for f, (tr_idx, te_idx) in enumerate(folds):
        train, test = base.iloc[tr_idx], base.iloc[te_idx]
        for name, kw in variants.items():
            model, tr_feat = fit_vitals_model(train, horizon=H, window=cfg["baseline_window_days"], **kw)
            te = model.score(test)
            te["fold"] = f
            scored[name].append(te)
            if name == "full":
                fold_info.append({"fold": f, "train_patients": int(train.patient_id.nunique()), "test_patients": int(test.patient_id.nunique()),
                                  **model.info})
                # XGBoost baseline on the same personal features + label history (same fold, same information)
                trx = history_features(cohort.add_targets(tr_feat, H))
                tex = history_features(cohort.add_targets(te.copy(), H))
                cols = readings_features() + ["label_today", "labels_7d", "label_rate", "has_reading"]
                clf = xgb.XGBClassifier(n_estimators=300, max_depth=3, learning_rate=0.03, subsample=0.8, colsample_bytree=0.8,
                                        random_state=0, verbosity=0)
                clf.fit(trx[cols].fillna(0), trx["y_ahead"])
                tex["xgb"] = clf.predict_proba(tex[cols].fillna(0))[:, 1]
                xgb_parts.append(tex[["patient_id", "day", "xgb"]])
                # ECHOS model: personal Bayesian event rate (empirical-Bayes Beta prior from the training patients)
                prior = R.fit_from_frame(train)
                fold_info[-1]["beta_prior"] = prior.to_dict()
                te2 = te.sort_values(["patient_id", "day"]).copy()
                te2["beta_rate"] = np.nan
                for _, idx in te2.groupby("patient_id").indices.items():
                    te2.iloc[idx, te2.columns.get_loc("beta_rate")] = R.personal_rate(te2["event"].values[idx], prior)
                te2["beta_forecast"] = R.forecast(te2["beta_rate"].values, H)
                te2["day_index"] = te2.groupby("patient_id").cumcount()
                # ablation: the same rate + today's personal readings features (logistic stacking, fitted on the training patients)
                tr2 = tr_feat.sort_values(["patient_id", "day"]).copy()
                tr2["beta_rate"] = np.nan
                for _, idx in tr2.groupby("patient_id").indices.items():
                    tr2.iloc[idx, tr2.columns.get_loc("beta_rate")] = R.personal_rate(tr2["event"].values[idx], prior)
                tr2 = cohort.add_targets(tr2, H)
                stack_cols = ["logit_rate", "event", "has_reading"] + readings_features()
                for frame in (tr2, te2):
                    frame["logit_rate"] = np.log(frame["beta_rate"] / (1 - frame["beta_rate"]))
                st = make_pipeline(StandardScaler(), LogisticRegression(max_iter=4000, C=0.3)).fit(tr2[stack_cols].fillna(0), tr2["y_ahead"])
                te2["rate_plus_readings"] = st.predict_proba(te2[stack_cols].fillna(0))[:, 1]
                rate_parts.append(te2[["patient_id", "day", "beta_rate", "beta_forecast", "rate_plus_readings", "day_index"]])

    full = history_features(cohort.add_targets(pd.concat(scored["full"]), H))
    full = full.merge(pd.concat(xgb_parts), on=["patient_id", "day"], how="left")
    full = full.merge(pd.concat(rate_parts), on=["patient_id", "day"], how="left")
    for name in variants:
        if name != "full":
            other = cohort.add_targets(pd.concat(scored[name]), H)[["patient_id", "day", "forecast"]].rename(columns={"forecast": f"fc_{name}"})
            full = full.merge(other, on=["patient_id", "day"], how="left")
    s = cfg["safety"]
    full["threshold_today"] = (((full.sys > s["sys_high"]) | (full.sys < s["sys_low"]) | (full.dia > s["dia_high"]) | (full.dia < s["dia_low"])
                                | (full.hr > s["hr_high"]) | (full.hr < s["hr_low"])).astype(float))
    full["high_bp_160_100"] = ((full.sys >= 160) | (full.dia >= 100)).astype(float)
    y, g = full["y_ahead"].values, full["patient_id"].values
    jitter = 1e-6 * full["label_rate"].fillna(0).values   # break ties of 0/1 baselines by the patient's own label rate
    candidates = {
        "persistence (label today)": full["label_today"].values + jitter,
        "personal label rate": full["label_rate"].values,
        "TIHM safety threshold today": full["threshold_today"].values + jitter,
        "BP >= 160/100 today": full["high_bp_160_100"].values + jitter,
        "XGBoost (same features + history)": full["xgb"].values,
        "Ablation: HMM labels only, population": full["fc_labels_only_population"].values,
        "Ablation: HMM + personal onset": full["fc_no_readings"].values,
        "Ablation: HMM + readings": full["fc_no_personal_onset"].values,
        "Ablation: HMM + readings + personal onset": full["forecast"].values,
        "Ablation: Bayesian rate + today's readings": full["rate_plus_readings"].values,
        "ECHOS: personal Bayesian rate": full["beta_forecast"].values,
    }
    budget = cfg["alert_budget_per_patient_week"]
    results = {}
    for name, sc in candidates.items():
        r = M.summary(y, sc, 7, budget, prob=name.startswith(("Ablation", "ECHOS", "XGB")))
        r["ci95"] = M.bootstrap_ci(y, sc, g, 7, budget, n_boot=n_boot)
        results[name] = r
    ours = candidates["ECHOS: personal Bayesian rate"]
    paired = {k: M.paired_diff_ci(y, ours, v, g, n_boot=n_boot) for k, v in candidates.items() if not k.startswith("ECHOS")}
    # calibration table of the forecast (deciles)
    q = pd.qcut(full["beta_forecast"], 10, duplicates="drop")
    calib = full.groupby(q, observed=True).agg(mean_forecast=("beta_forecast", "mean"), observed_rate=("y_ahead", "mean"), n=("y_ahead", "size"))
    # new patients: the first 7 / 21 days of each patient's record (where pooling matters)
    cold = {}
    for lim in (7, 21):
        m = (full["day_index"] < lim).values
        cold[f"first_{lim}_days"] = {"rows": int(m.sum()), "positives": int(y[m].sum()),
                                     **{k: float(M.summary(y[m], candidates[k][m], 7, budget, prob=False)["pr_auc"]) for k in
                                        ("ECHOS: personal Bayesian rate", "personal label rate", "persistence (label today)", "XGBoost (same features + history)")}}
    out = {
        "task": f"validated BP/pulse label on days t+1..t+{H}, from data up to day t",
        "cohort": {"patients": int(full.patient_id.nunique()), "patient_days": int(len(full)), "positives": int(y.sum()),
                   "prevalence": float(y.mean()), "event_days": int(full.event.sum()), "min_bp_days": cfg["min_bp_days"],
                   "patient_weeks": float(len(full) / 7)},
        "alert_budget_per_patient_week": budget,
        "results": results,
        "ours_minus_baseline_pr_auc": paired,
        "new_patients": cold,
        "calibration_deciles": calib.reset_index(drop=True).to_dict(orient="records"),
        "folds": fold_info,
        "runtime_s": round(time.time() - t0, 1),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2))
    OOF.parent.mkdir(parents=True, exist_ok=True)   # patient-level rows: git-ignored data/v2/, used by allocation_eval.py
    full[["patient_id", "day", "event", "y_ahead", "beta_rate", "beta_forecast", "sys", "dia", "hr", "sys_usual", "dia_usual", "hr_usual",
          "sys_z", "dia_z", "hr_z"]].to_parquet(OOF, index=False)
    print(f"cohort: {out['cohort']}")
    for k, r in results.items():
        b = r["at_budget"]
        print(f"{k:46s} PR-AUC {r['pr_auc']:.3f} [{r['ci95']['pr_auc'][0]:.3f}-{r['ci95']['pr_auc'][1]:.3f}]  ROC {r['roc_auc']:.3f}  "
              f"recall {b['recall']:.2f} prec {b['precision']:.2f}")
    print("new patients:", json.dumps(cold))
    for k, v in paired.items():
        print(f"  ours - {k:40s} {v['diff']:+.3f} CI [{v['ci95'][0]:+.3f}, {v['ci95'][1]:+.3f}] better={v['better']}")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--boot", type=int, default=1000)
    main(ap.parse_args().boot)
