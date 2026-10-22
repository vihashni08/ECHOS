"""Evaluate the agitation detector (5-fold, split by PATIENT) -> reports/metrics/v2_agitation.json.

Task: is there a confirmed agitation episode in this activity window (night 18-08, morning 08-12, afternoon 12-18)?
Rows: every window of every patient with activity data. Budget: 1 alert per patient-week = 1 per 21 windows.

    PYTHONPATH=. python scripts/evaluation/agitation_eval.py [--boot 1000]
"""
from __future__ import annotations

import argparse
import json
import time

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from echos.config import ROOT, load_config
from echos.data import loaders
from echos.data.splits import patient_folds
from echos.monitor import agitation as A, cohort, metrics as M

OUT = ROOT / "reports" / "metrics" / "v2_agitation.json"
OOF = ROOT / "data" / "v2" / "oof_agitation.parquet"
UPW = 21  # windows per patient-week


def oof_scores(t: pd.DataFrame, folds, features: list[str], kind: str = "xgb") -> np.ndarray:
    s = np.zeros(len(t))
    for tr, te in folds:
        if kind == "xgb":
            m = A.AgitationModel(features).fit(t.iloc[tr])
            s[te] = m.predict_proba(t.iloc[te])
        else:
            m = make_pipeline(StandardScaler(), LogisticRegression(max_iter=5000, C=0.3, class_weight="balanced"))
            m.fit(t.iloc[tr][features].fillna(0), t.iloc[tr]["y"])
            s[te] = m.predict_proba(t.iloc[te][features].fillna(0))[:, 1]
    return s


def main(n_boot: int = 1000) -> dict:
    t0 = time.time()
    t = A.add_slot_dummies(A.build_table())
    # same-day vitals as optional extra inputs (ablation): daily max BP / HR and mean temperature, matched on the window's day
    dv = cohort.daily_vitals()[["patient_id", "day", "sys", "dia", "hr", "temp"]]
    t["day"] = (t["w"] - pd.Timedelta(hours=1)).dt.normalize()
    t = t.merge(dv, on=["patient_id", "day"], how="left")
    t["has_vitals"] = t["sys"].notna().astype(int)
    t = t.sort_values(["patient_id", "w"]).reset_index(drop=True)
    folds = list(patient_folds(t, load_config()["splits"]["n_folds"], load_config()["seed"]))
    fs = A.feature_sets()
    vit = ["sys", "dia", "hr", "temp", "has_vitals"]
    runs = {
        "time slot only": (fs["slot"], "xgb"),
        "episode history only": (fs["history"], "xgb"),
        "activity counts, no personal baseline": (fs["raw"] + fs["slot"], "xgb"),
        "personal baseline z only": (fs["personal_z"], "xgb"),
        "logistic regression (full features)": (fs["full"], "logreg"),
        "Ablation: ECHOS without history": (fs["raw+z+slot"], "xgb"),
        "Ablation: ECHOS + same-day vitals": (fs["full"] + vit, "xgb"),
        "ECHOS agitation detector": (fs["full"], "xgb"),
    }
    y, g = t["y"].values, t["patient_id"].values
    scores, results = {}, {}
    for name, (feats, kind) in runs.items():
        scores[name] = oof_scores(t, folds, feats, kind)
        r = M.summary(y, scores[name], UPW, 1.0)
        r["ci95"] = M.bootstrap_ci(y, scores[name], g, UPW, 1.0, n_boot=n_boot)
        results[name] = r
        b = r["at_budget"]
        print(f"{name:42s} PR-AUC {r['pr_auc']:.3f} [{r['ci95']['pr_auc'][0]:.3f}-{r['ci95']['pr_auc'][1]:.3f}] ROC {r['roc_auc']:.3f} "
              f"recall {b['recall']:.2f} prec {b['precision']:.2f} caught {b['caught']}/{b['positives']}")
    ours = scores["ECHOS agitation detector"]
    paired = {k: M.paired_diff_ci(y, ours, v, g, n_boot=n_boot) for k, v in scores.items() if k != "ECHOS agitation detector"}
    for k, v in paired.items():
        print(f"  ours - {k:40s} {v['diff']:+.3f} CI [{v['ci95'][0]:+.3f}, {v['ci95'][1]:+.3f}] better={v['better']}")
    # robustness: drop the patient with the most labels; per-slot results
    top = t.groupby("patient_id")["y"].sum().idxmax()
    m = g != top
    robust = {"without_patient_with_most_labels": {"labels_removed": int(t.loc[~m, "y"].sum()),
                                                   **{k: v for k, v in M.summary(y[m], ours[m], UPW, 1.0).items() if k in ("pr_auc", "roc_auc", "prevalence")}}}
    per_slot = {}
    for h in (8, 12, 18):
        ms = (t["slot"] == h).values
        if y[ms].sum() > 0:
            per_slot[{8: "night 18-08", 12: "morning 08-12", 18: "afternoon 12-18"}[h]] = {
                "windows": int(ms.sum()), "positives": int(y[ms].sum()), "pr_auc": float(M.summary(y[ms], ours[ms], UPW, prob=False)["pr_auc"])}
    q = pd.qcut(pd.Series(ours), 10, duplicates="drop")
    calib = pd.DataFrame({"p": ours, "y": y}).groupby(q.values, observed=True).agg(mean_prob=("p", "mean"), observed_rate=("y", "mean"), n=("y", "size"))
    out = {
        "task": "confirmed agitation episode in this activity window (night 18-08 / 08-12 / 12-18)",
        "data": {"activity_events": int(len(loaders.load_activity())), "windows": int(len(t)), "patients": int(t.patient_id.nunique()),
                 "patients_with_episodes": int(t[t.y == 1].patient_id.nunique()), "positives": int(y.sum()), "prevalence": float(y.mean()),
                 "patient_weeks": float(len(t) / UPW)},
        "alert_budget_per_patient_week": 1.0,
        "results": results,
        "ours_minus_baseline_pr_auc": paired,
        "robustness": robust,
        "per_slot": per_slot,
        "calibration_deciles": calib.reset_index(drop=True).to_dict(orient="records"),
        "runtime_s": round(time.time() - t0, 1),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2))
    OOF.parent.mkdir(parents=True, exist_ok=True)
    t.assign(p_agitation=ours)[["patient_id", "w", "slot", "y", "p_agitation"]].to_parquet(OOF, index=False)
    print(json.dumps({"data": out["data"], "robustness": robust, "per_slot": per_slot}, indent=1))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--boot", type=int, default=1000)
    main(ap.parse_args().boot)
