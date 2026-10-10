"""Leakage audit for Agent 2 (no tuning): within-patient AUCs, feature importance, no-absolute-level ablation."""
import json
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score
from echos.agents import behaviour as b
from echos.config import load_config
from echos.data.splits import patient_folds


def oof(df, feats, seed, nf):
    y = df.y.values
    o = np.zeros(len(df))
    for tr, te in patient_folds(df, nf, seed):
        m = b._model(seed, (1 - y[tr].mean()) / max(y[tr].mean(), 1e-6)).fit(df.iloc[tr][feats], y[tr])
        o[te] = m.predict_proba(df.iloc[te][feats])[:, 1]
    return o


def within(df, s):
    r = []
    for p, g in df.assign(s=s).groupby("patient_id"):
        if 0 < g.y.sum() < len(g):
            r.append((roc_auc_score(g.y, g.s), average_precision_score(g.y, g.s), g.y.mean()))
    a = np.array(r)
    q = lambda x: [float(np.median(x)), float(np.percentile(x, 25)), float(np.percentile(x, 75))]
    return {"n_patients": len(a), "roc_auc_median_q1_q3": q(a[:, 0]), "pr_auc_median_q1_q3": q(a[:, 1]),
            "patient_prevalence_median": float(np.median(a[:, 2]))}


cfg = load_config()
seed, nf = cfg["seed"], cfg["splits"]["n_folds"]
df = b.build_features()
df = df[df.has_activity & df.total_events_delta.notna() & df.total_events_z.notna()].reset_index(drop=True)
y = df.y.values
full = oof(df, b.CLF_FEATURES, seed, nf)
zf = [f"{f}_z" for f in b.FEATS]
zo = oof(df, zf, seed, nf)
res = {}
for name, s in [("full", full), ("z_only", zo), ("drift_score_only", df.drift_score.values)]:
    res[name] = {"pooled_roc_auc": float(roc_auc_score(y, s)), "pooled_pr_auc": float(average_precision_score(y, s)),
                 "within_patient": within(df, s)}
m = b._model(seed, (1 - y.mean()) / y.mean()).fit(df[b.CLF_FEATURES], y)
imp = pd.Series(m.get_booster().get_score(importance_type="gain")).reindex(b.CLF_FEATURES).fillna(0)
imp = (imp / imp.sum()).sort_values(ascending=False)
res["top10_gain_importance"] = {k: float(v) for k, v in imp.head(10).items()}
res["prevalence"] = float(y.mean())
json.dump(res, open("/tmp/x", "w")) if False else None
a = json.load(open(b.METRICS_PATH))
a["within_patient_audit"] = res
json.dump(a, open(b.METRICS_PATH, "w"), indent=2)
print("model            pooledROC pooledPR | within ROC med [IQR] | within PR med [IQR] | n")
for k in ("full", "z_only", "drift_score_only"):
    r, w = res[k], res[k]["within_patient"]
    f = lambda t: f"{t[0]:.3f} [{t[1]:.3f}-{t[2]:.3f}]"
    print(f"{k:<16} {r['pooled_roc_auc']:.3f}     {r['pooled_pr_auc']:.3f}   | {f(w['roc_auc_median_q1_q3'])} | {f(w['pr_auc_median_q1_q3'])} | {w['n_patients']}")
print("top10 gain:", ", ".join(f"{k} {v:.2f}" for k, v in res["top10_gain_importance"].items()))
