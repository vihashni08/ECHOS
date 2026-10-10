"""Agent 2 - Behaviour. Routine-drift score (past-only z + one-sided CUSUM) and same-day agitation classifier."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import average_precision_score, roc_auc_score

from echos.config import ROOT, load_config
from echos.data.patient_day import load_patient_day
from echos.data.splits import patient_folds
from echos.schemas import AgentResult

AGENT = "behaviour"
# feature -> direction of clinically relevant drift (+1 = increase, -1 = decrease)
DIRECTION = {"total_events": -1, "night_events": +1, "night_bathroom": +1, "night_door": +1,
             "kitchen": -1, "fridge": -1, "room_transitions": -1}
SRC = {"kitchen": "n_kitchen", "fridge": "n_fridge_door"}
FEATS = list(DIRECTION)
Z_CLIP = 4.0
MODEL_PATH = ROOT / "models" / "behaviour.joblib"
METRICS_PATH = ROOT / "reports" / "metrics" / "agent2.json"
CLF_FEATURES = ([f"{f}_z" for f in FEATS] + [f"{f}_cusum" for f in FEATS] + FEATS
                + ["drift_score", "n_drifted", "total_events_delta", "night_events_delta"])


def _patient(g: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    g = g.sort_values("day").copy()
    k, h = cfg["cusum"]["k"], cfg["cusum"]["h"]
    for f in FEATS:
        g[f] = g[SRC.get(f, f)].where(g.has_activity)
    for f in FEATS:
        past = g[f].shift(1)  # strictly previous days only
        roll = past.rolling(cfg["baseline_window_days"], min_periods=cfg["min_baseline_days"])
        sd = np.maximum(roll.std(), 0.1 * roll.mean().abs() + 1.0)  # scale floor avoids blow-ups on sparse counts
        g[f"{f}_z"] = ((g[f] - roll.mean()) / sd).clip(-Z_CLIP, Z_CLIP)
        s, out = 0.0, []
        for z in g[f"{f}_z"].values:  # one-sided CUSUM in the direction of concern; missing day = no update
            if not np.isnan(z):
                s = max(0.0, s + DIRECTION[f] * z - k)
            out.append(s)
        g[f"{f}_cusum"] = out
    cu = g[[f"{f}_cusum" for f in FEATS]]
    g["drift_score"] = np.minimum(cu.max(axis=1) / (2 * h), 1.0)
    g["n_drifted"] = (cu >= h).sum(axis=1)
    g["total_events_delta"] = g["total_events"] - g["total_events"].shift(1)
    g["night_events_delta"] = g["night_events"] - g["night_events"].shift(1)
    return g


def build_features(pdy: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    cfg = load_config()["agent2"]
    pdy = load_patient_day() if pdy is None else pdy
    out = pd.concat([_patient(g, cfg) for _, g in pdy.groupby("patient_id")]).reset_index(drop=True)
    out["y"] = (out.agitation > 0).astype(int)
    return out


def _model(seed: int, pw: float) -> xgb.XGBClassifier:
    return xgb.XGBClassifier(n_estimators=150, max_depth=2, learning_rate=0.05, subsample=0.8,
                             colsample_bytree=0.8, min_child_weight=5, scale_pos_weight=pw,
                             eval_metric="aucpr", random_state=seed, n_jobs=2)


def _topk(y, s, k, pw):
    k = int(min(max(k, 1), len(y)))
    top = np.argsort(-s, kind="stable")[:k]
    tp = int(y[top].sum())
    return {"recall": tp / max(y.sum(), 1), "precision": tp / k, "false_alerts_per_patient_week": (k - tp) / pw}


def evaluate() -> dict:
    cfg = load_config()
    seed, a2 = cfg["seed"], cfg["agent2"]
    df = build_features()
    df = df[df.has_activity & df.total_events_delta.notna() & df.total_events_z.notna()].reset_index(drop=True)
    y = df.y.values
    oof = np.zeros(len(df))
    for tr, te in patient_folds(df, cfg["splits"]["n_folds"], seed):
        m = _model(seed, (1 - y[tr].mean()) / max(y[tr].mean(), 1e-6))
        m.fit(df.iloc[tr][CLF_FEATURES], y[tr])
        oof[te] = m.predict_proba(df.iloc[te][CLF_FEATURES])[:, 1]
    drift = df.drift_score.values
    budgets = a2["alert_budget_per_patient_week"]

    def met(idx):
        yy, ss, dd = y[idx], oof[idx], drift[idx]
        pw = len(idx) / 7.0
        o = {"prevalence": float(yy.mean()), "pr_auc": float(average_precision_score(yy, ss)),
             "roc_auc": float(roc_auc_score(yy, ss)),
             "drift_only_pr_auc": float(average_precision_score(yy, dd)),
             "drift_only_roc_auc": float(roc_auc_score(yy, dd))}
        for b in budgets:
            o[f"recall@{b}"] = _topk(yy, ss, b * pw, pw)["recall"]
        return o

    point = met(np.arange(len(df)))
    rng = np.random.RandomState(seed)
    pids = df.patient_id.unique()
    groups = {p: np.where(df.patient_id.values == p)[0] for p in pids}
    boots = []
    for _ in range(300):
        idx = np.concatenate([groups[p] for p in rng.choice(pids, len(pids))])
        if 0 < y[idx].sum() < len(idx):
            boots.append(met(idx))
    bdf = pd.DataFrame(boots)
    ci = {c: [float(bdf[c].quantile(.025)), float(bdf[c].quantile(.975))] for c in bdf.columns}

    pw = len(df) / 7.0
    table = {f"at_{b}_alerts_per_patient_week": _topk(y, oof, b * pw, pw) for b in budgets}
    final = _model(seed, (1 - y.mean()) / y.mean()).fit(df[CLF_FEATURES], y)
    threshold = float(np.sort(oof)[::-1][min(int(pw), len(oof)) - 1])  # 1 alert / patient-week
    MODEL_PATH.parent.mkdir(exist_ok=True)
    joblib.dump({"model": final, "features": CLF_FEATURES, "threshold": threshold}, MODEL_PATH)
    res = {"n_days": int(len(df)), "n_patients": int(len(pids)), "n_positive": int(y.sum()),
           "n_patients_with_agitation": int(df[df.y == 1].patient_id.nunique()),
           "patient_weeks": pw, "point": point, "bootstrap_95ci": ci, "alert_budget_table": table,
           "threshold_at_1_per_week": threshold,
           "notes": ["OOF predictions, patient-level GroupKFold; CI = patient bootstrap (300)",
                     "z/CUSUM use past-only rolling 28d baseline (shift(1)); same-day label = detection, not forecast",
                     "wide CIs: only ~27 patients have an agitation day"]}
    METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    METRICS_PATH.write_text(json.dumps(res, indent=2))
    return res


_cache: dict = {}


def _state():
    if not _cache:
        _cache["art"] = joblib.load(MODEL_PATH)
        df = build_features()
        _cache["df"] = df.set_index(["patient_id", "day"])
    return _cache


def predict(patient_id: str, day) -> AgentResult:
    st = _state()
    d = pd.Timestamp(day).normalize()
    ts = datetime.utcnow()
    if (patient_id, d) not in st["df"].index or not st["df"].loc[(patient_id, d), "has_activity"]:
        return AgentResult(patient_id=patient_id, day=d.date(), agent=AGENT, score=0.0, flag=False,
                           evidence={"reason": "no_activity_data"}, ts=ts)
    art, r = st["art"], st["df"].loc[(patient_id, d)]
    X = pd.DataFrame([r[art["features"]].astype(float)])
    prob = float(art["model"].predict_proba(X)[0, 1]) if not X.isna().all(axis=None) and not np.isnan(r["total_events_z"]) else 0.0
    h = load_config()["agent2"]["cusum"]["h"]
    drifted = [f for f in FEATS if r[f"{f}_cusum"] >= h]
    ev = {"drift_score": float(r["drift_score"]), "agitation_prob": prob, "drifted_features": drifted,
          "cusum": {f: round(float(r[f"{f}_cusum"]), 2) for f in FEATS},
          "z": {f: None if np.isnan(r[f"{f}_z"]) else round(float(r[f"{f}_z"]), 2) for f in FEATS},
          "threshold": art["threshold"]}
    return AgentResult(patient_id=patient_id, day=d.date(), agent=AGENT, score=prob,
                       flag=bool(prob >= art["threshold"] or drifted), evidence=ev, ts=ts)


if __name__ == "__main__":
    r = evaluate()
    p, ci = r["point"], r["bootstrap_95ci"]
    print(f"days={r['n_days']} pos={r['n_positive']} ({p['prevalence']:.1%}) pts_with_agitation={r['n_patients_with_agitation']}")
    print(f"{'metric':<22}{'value':>8}{'95% CI':>18}")
    for k in ["pr_auc", "roc_auc", "drift_only_pr_auc", "drift_only_roc_auc"] + [c for c in p if c.startswith("recall@")]:
        print(f"{k:<22}{p[k]:>8.3f}{ci[k][0]:>10.3f}-{ci[k][1]:.3f}")
    for k, v in r["alert_budget_table"].items():
        print(f"{k}: recall {v['recall']:.3f}, FA/pt-wk {v['false_alerts_per_patient_week']:.2f}")
