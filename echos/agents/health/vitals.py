"""Health Monitoring sub-model 1 - Vitals. Day-level alert detection from BP / heart-rate readings.

All baseline features are computed from PAST reading-days only (shift / closed='left').
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import average_precision_score

from echos.agents import oof
from echos.data.guard import assert_real_source
from echos.config import ROOT, load_config
from echos.data.patient_day import load_patient_day
from echos.data.splits import patient_folds
from echos.schemas import AgentResult

AGENT = "physiological"  # name kept: oof_scores.parquet and the saved model are keyed on it
VITALS = ["sys", "dia", "hr"]
FEATURES = (
    [f"{v}_{s}" for v in VITALS for s in ("base", "z", "delta_prev", "trend7")]
    + ["sys", "dia", "hr", "pulse_pressure", "pp_dev", "days_since_last", "n_prior"]
)
MODEL_PATH = ROOT / "models" / "physiological.joblib"
CALIBRATED_PATH = ROOT / "models" / "physiological_calibrated.joblib"   # written by scripts/tuning/calibrate.py (old model kept)
METRICS_PATH = ROOT / "reports" / "metrics" / "agent1.json"
MIN_SCALE = {"sys": 3.0, "dia": 2.0, "hr": 2.0}  # floor on robust scale (units) to avoid z blow-ups


def _slope(s: pd.Series) -> float:
    if len(s) < 3:
        return np.nan
    x = (s.index - s.index[0]).days.values.astype(float)
    return float(np.polyfit(x, s.values, 1)[0]) if x.std() > 0 else np.nan


def _mad(a: np.ndarray) -> float:
    m = np.nanmedian(a)
    return float(np.nanmedian(np.abs(a - m)))


def _patient_features(g: pd.DataFrame, window: int) -> pd.DataFrame:
    g = g.sort_values("day").copy()
    for v in VITALS:
        s = g[v]
        past = s.shift(1)  # strictly previous reading-days
        g[f"{v}_base"] = past.rolling(window, min_periods=3).median()
        mad = past.rolling(window, min_periods=3).apply(_mad, raw=True)
        scale = np.maximum(1.4826 * mad, MIN_SCALE[v])
        g[f"{v}_z"] = (s - g[f"{v}_base"]) / scale
        g[f"{v}_delta_prev"] = s - past
        ts = s.set_axis(g["day"].values).dropna()
        tr = ts.rolling("7D", closed="left").apply(_slope, raw=False)  # slope of prior 7 days
        g[f"{v}_trend7"] = tr.reindex(g["day"].values).values
    g["pulse_pressure"] = g["sys"] - g["dia"]
    pp_base = g["pulse_pressure"].shift(1).rolling(window, min_periods=3).median()
    g["pp_dev"] = g["pulse_pressure"] - pp_base
    g["days_since_last"] = (g["day"] - g["day"].shift(1)).dt.days
    g["n_prior"] = np.arange(len(g))
    return g


def build_features(pdy: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """One row per reading-day (day with sys/dia/hr) with past-only features and target."""
    cfg = load_config()["agent1"]
    pdy = load_patient_day() if pdy is None else pdy
    rd = pdy[pdy.has_reading].copy()
    rd["y"] = ((rd.bp_alert + rd.pulse_alert + rd.water_alert) > 0).astype(int)
    parts = [_patient_features(g, cfg["baseline_window_days"]) for _, g in rd.groupby("patient_id")]
    return pd.concat(parts).reset_index(drop=True)


def fixed_rule(df: pd.DataFrame) -> np.ndarray:
    r = load_config()["agent1"]["fixed_rule"]
    return ((df.sys >= r["sys_high"]) | (df.dia >= r["dia_high"]) | (df.sys < r["sys_low"])).values


def _model(seed: int, pos_weight: float) -> xgb.XGBClassifier:
    return xgb.XGBClassifier(n_estimators=200, max_depth=3, learning_rate=0.05, subsample=0.8,
                             colsample_bytree=0.8, min_child_weight=3, scale_pos_weight=pos_weight,
                             eval_metric="aucpr", random_state=seed, n_jobs=2)


def _budget_stats(y, score, k, pw):
    """Recall / false alerts per patient-week when alerting on the top-k scores."""
    k = int(min(max(k, 1), len(y)))
    top = np.argsort(-score, kind="stable")[:k]
    tp = int(y[top].sum())
    return {"alerts": k, "recall": tp / max(y.sum(), 1), "precision": tp / k,
            "false_alerts_per_patient_week": (k - tp) / pw}


def evaluate() -> dict:
    cfg = load_config()
    seed = cfg["seed"]
    pdy = load_patient_day()
    df = build_features(pdy)
    assert_real_source(df, "tihm")
    pw_by_patient = pdy.groupby("patient_id").size() / 7.0  # monitored patient-weeks
    y = df.y.values
    oof = np.zeros(len(df))
    for tr, te in patient_folds(df, cfg["splits"]["n_folds"], seed):
        m = _model(seed, (1 - y[tr].mean()) / max(y[tr].mean(), 1e-6))
        m.fit(df.iloc[tr][FEATURES], y[tr])
        oof[te] = m.predict_proba(df.iloc[te][FEATURES])[:, 1]
    rule = fixed_rule(df).astype(float)
    pw = float(pw_by_patient.loc[df.patient_id.unique()].sum())
    budgets = cfg["agent1"]["alert_budget_per_patient_week"]
    rule_stats = _budget_stats(y, rule + 1e-9 * oof, int(rule.sum()), pw)  # ties in rule broken by model

    def metrics(idx, pwi=None):
        yy, ss, rr = y[idx], oof[idx], rule[idx]
        pwi = pw if pwi is None else pwi
        out = {"prevalence": float(yy.mean()),
               "xgb_pr_auc": float(average_precision_score(yy, ss)),
               "rule_pr_auc": float(average_precision_score(yy, rr))}
        for b in budgets:
            out[f"xgb_recall@{b}"] = _budget_stats(yy, ss, b * pwi, pwi)["recall"]
        k_rule = int(rr.sum())
        out["rule_recall"] = float(yy[rr > 0].sum() / max(yy.sum(), 1))
        out["xgb_recall@rule_budget"] = _budget_stats(yy, ss, k_rule, pwi)["recall"]
        return out

    point = metrics(np.arange(len(df)))
    # patient-level bootstrap for CIs
    rng = np.random.RandomState(seed)
    pids = df.patient_id.unique()
    groups = {p: np.where(df.patient_id.values == p)[0] for p in pids}
    boots = []
    for _ in range(200):
        samp = rng.choice(pids, len(pids))
        idx = np.concatenate([groups[p] for p in samp])
        if y[idx].sum() == 0:
            continue
        try:
            boots.append(metrics(idx, float(pw_by_patient.loc[samp].sum())))  # duplicates counted
        except Exception:
            pass
    bdf = pd.DataFrame(boots)
    ci = {c: [float(bdf[c].quantile(.025)), float(bdf[c].quantile(.975))] for c in bdf.columns}

    table = {"fixed_rule": {"alerts": int(rule.sum()), "recall": point["rule_recall"],
                            "precision": float(y[rule > 0].mean()) if rule.sum() else 0.0,
                            "false_alerts_per_patient_week": float((rule.sum() - y[rule > 0].sum()) / pw),
                            "pr_auc": point["rule_pr_auc"]},
             "xgboost": {"pr_auc": point["xgb_pr_auc"],
                         "at_rule_budget": _budget_stats(y, oof, int(rule.sum()), pw)}}
    for b in budgets:
        table["xgboost"][f"at_{b}_alerts_per_patient_week"] = _budget_stats(y, oof, b * pw, pw)

    # final model + threshold at the 1 alert / patient-week budget
    final = _model(seed, (1 - y.mean()) / y.mean())
    final.fit(df[FEATURES], y)
    k1 = int(1.0 * pw)
    threshold = float(np.sort(oof)[::-1][min(k1, len(oof)) - 1])
    import shap
    expl = shap.TreeExplainer(final)
    sv = expl.shap_values(df[FEATURES])
    glob = pd.Series(np.abs(sv).mean(0), index=FEATURES).sort_values(ascending=False)
    MODEL_PATH.parent.mkdir(exist_ok=True)
    joblib.dump({"model": final, "features": FEATURES, "threshold": threshold}, MODEL_PATH)

    res = {"n_reading_days": int(len(df)), "n_patients": int(len(pids)), "n_positive": int(y.sum()),
           "patient_weeks": pw, "prevalence": point["prevalence"], "threshold_at_1_per_week": threshold,
           "results": table, "bootstrap_95ci": ci, "shap_global_top5": glob.head(5).round(4).to_dict(),
           "notes": ["OOF predictions from patient-level GroupKFold(5, seed 42)",
                     "patient_weeks = all monitored patient-days / 7; scoring only on days with a vitals reading",
                     "target includes water_alert, which has no BP/HR signal in these features",
                     "~49 labelled patients: wide CIs; labels likely derive from clinical thresholds"]}
    METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    METRICS_PATH.write_text(json.dumps(res, indent=2))
    return res


# ---------------------------------------------------------------- inference
_cache: dict = {}


def _state():
    if not _cache:
        use_cal = (load_config().get("health") or {}).get("use_calibrated", False) and CALIBRATED_PATH.exists()
        _cache["art"] = joblib.load(CALIBRATED_PATH if use_cal else MODEL_PATH)
        df = build_features()
        df["day"] = pd.to_datetime(df["day"])
        _cache["df"] = df.set_index(["patient_id", "day"])
        import shap
        _cache["expl"] = shap.TreeExplainer(_cache["art"]["model"])
    return _cache


def predict(patient_id: str, day) -> AgentResult:
    st = _state()
    d = pd.Timestamp(day).normalize()
    ts = datetime.utcnow()
    if (patient_id, d) not in st["df"].index:
        return AgentResult(patient_id=patient_id, day=d.date(), agent=AGENT, score=0.0, flag=False,
                           evidence={"reason": "no_vitals_reading"}, ts=ts)
    return _score_row(patient_id, d, st["df"].loc[[(patient_id, d)]], st, ts, use_oof=True)


def _score_row(patient_id: str, d: pd.Timestamp, row: pd.DataFrame, st: dict, ts, use_oof: bool = False) -> AgentResult:
    art = st["art"]
    X = row[art["features"]]
    score = float(art["model"].predict_proba(X)[0, 1])
    if "calibrator" in art:
        score = float(art["calibrator"](np.array([score]))[0])
    if use_oof and oof.enabled():  # replay evaluation with out-of-fold scores
        score = float(oof.lookup(AGENT, patient_id, d) or 0.0)
    sv = st["expl"].shap_values(X)[0]
    top = np.argsort(-np.abs(sv))[:3]
    r = row.iloc[0]
    ev = {
        "top_features": [{"feature": art["features"][i],
                          "value": None if pd.isna(r[art["features"][i]]) else float(r[art["features"][i]]),
                          "shap": float(sv[i])} for i in top],
        "current_vs_baseline": {v: {"current": None if pd.isna(r[v]) else float(r[v]),
                                    "baseline": None if pd.isna(r[f"{v}_base"]) else float(r[f"{v}_base"]),
                                    "z": None if pd.isna(r[f"{v}_z"]) else float(r[f"{v}_z"])} for v in VITALS},
        "fixed_rule_triggered": bool(fixed_rule(row)[0]),
        "threshold": art["threshold"],
        "n_prior_readings": int(r["n_prior"]),
    }
    return AgentResult(patient_id=patient_id, day=d.date(), agent=AGENT, score=score,
                       flag=score >= art["threshold"], evidence=ev, ts=ts)


def predict_live(patient_id: str, day, hist: pd.DataFrame) -> tuple:
    """Score a day from the patient's own history rows (patient-day rows, past days + today, no parquet lookup).
    Returns (AgentResult, persistence dict). Same features and model as predict(); baselines use only days before `day`."""
    st = _state()
    d = pd.Timestamp(day).normalize()
    rd = hist[hist.has_reading & (hist.day <= d)].copy()
    if rd.empty or rd.day.max() != d:
        return AgentResult(patient_id=patient_id, day=d.date(), agent=AGENT, score=0.0, flag=False, evidence={"reason": "no_vitals_reading"}), {}
    g = _patient_features(rd.assign(patient_id=patient_id), load_config()["agent1"]["baseline_window_days"]).set_index("day")
    row = g.loc[[d]]
    res = _score_row(patient_id, d, row, st, datetime.utcnow())
    cfg = (load_config().get("health") or {}).get("persistence", {})
    z_thr, last_n, need = cfg.get("z", 2.0), cfg.get("last_n", 3), cfg.get("need", 2)
    tail = g.sort_index().tail(last_n)
    beyond = [bool(np.nanmax(np.abs(r[[f"{v}_z" for v in VITALS]].astype(float).values)) >= z_thr) if r[[f"{v}_z" for v in VITALS]].notna().any() else False for _, r in tail.iterrows()]
    pers = {"persistent": sum(beyond) >= need, "n_beyond": int(sum(beyond)), "n_readings": int(len(tail)), "last_n": last_n, "need": need, "z_threshold": z_thr}
    return res, pers


def persistence(patient_id: str, day, z_thr: float = 2.0, last_n: int = 3, need: int = 2) -> dict:
    """Evidence only (not a model feature): True when `need` of the last `last_n` readings up to `day` are beyond the
    patient's own past-only baseline band (any vital with |z| >= z_thr). Uses the same past-only z as the model."""
    df = _state()["df"]
    d = pd.Timestamp(day).normalize()
    if patient_id not in df.index.get_level_values(0):
        return {"persistent": False, "n_beyond": 0, "n_readings": 0, "last_n": last_n, "need": need, "z_threshold": z_thr}
    g = df.loc[patient_id]
    g = g[g.index <= d].sort_index().tail(last_n)
    beyond = [bool(np.nanmax(np.abs(r[[f"{v}_z" for v in VITALS]].astype(float).values)) >= z_thr) if r[[f"{v}_z" for v in VITALS]].notna().any() else False
              for _, r in g.iterrows()]
    return {"persistent": sum(beyond) >= need, "n_beyond": int(sum(beyond)), "n_readings": int(len(g)),
            "last_n": last_n, "need": need, "z_threshold": z_thr}


if __name__ == "__main__":
    r = evaluate()
    t = r["results"]
    ci = r["bootstrap_95ci"]
    print(f"reading-days={r['n_reading_days']} pos={r['n_positive']} ({r['prevalence']:.1%}) patient-weeks={r['patient_weeks']:.0f}")
    print(f"{'method':<28}{'PR-AUC':>8}{'recall':>8}{'FA/pt-wk':>10}")
    fr = t["fixed_rule"]
    print(f"{'fixed rule':<28}{fr['pr_auc']:>8.3f}{fr['recall']:>8.3f}{fr['false_alerts_per_patient_week']:>10.2f}")
    x = t["xgboost"]
    for name, k in [("xgb @rule budget", "at_rule_budget")] + [(f"xgb @{b}/wk", f"at_{b}_alerts_per_patient_week") for b in load_config()["agent1"]["alert_budget_per_patient_week"]]:
        s = x[k]
        print(f"{name:<28}{x['pr_auc']:>8.3f}{s['recall']:>8.3f}{s['false_alerts_per_patient_week']:>10.2f}")
    print(f"PR-AUC 95% CI: xgb {ci['xgb_pr_auc'][0]:.3f}-{ci['xgb_pr_auc'][1]:.3f}, rule {ci['rule_pr_auc'][0]:.3f}-{ci['rule_pr_auc'][1]:.3f}")
