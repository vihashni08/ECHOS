"""Evaluation helpers shared by the v2 evaluation scripts: PR-AUC, ROC-AUC, recall / precision at a fixed alert budget
(alerts per patient-week), false alerts per patient-week, Brier score, and patient-level bootstrap confidence intervals."""
from __future__ import annotations

import numpy as np
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score


def at_budget(y: np.ndarray, s: np.ndarray, units_per_week: float, budget: float = 1.0) -> dict:
    """Alert on the top-k scores where k = budget * (number of patient-weeks). units_per_week = rows per patient-week (7 for days,
    21 for 6-hour agitation windows)."""
    y, s = np.asarray(y), np.asarray(s, dtype=float)
    weeks = len(y) / units_per_week
    k = max(int(round(budget * weeks)), 1)
    top = np.argsort(-s, kind="stable")[:k]
    tp = int(y[top].sum())
    return {"alerts": k, "caught": tp, "positives": int(y.sum()), "recall": tp / max(int(y.sum()), 1), "precision": tp / k,
            "false_alerts_per_patient_week": (k - tp) / weeks}


def summary(y, s, units_per_week: float, budget: float = 1.0, prob: bool = True) -> dict:
    y, s = np.asarray(y), np.asarray(s, dtype=float)
    out = {"n": int(len(y)), "positives": int(y.sum()), "prevalence": float(y.mean()), "pr_auc": float(average_precision_score(y, s)),
           "roc_auc": float(roc_auc_score(y, s))}
    if prob and s.min() >= 0 and s.max() <= 1:
        out["brier"] = float(brier_score_loss(y, s))
    out["at_budget"] = at_budget(y, s, units_per_week, budget)
    return out


def bootstrap_ci(y, s, groups, units_per_week: float, budget: float = 1.0, n_boot: int = 1000, seed: int = 0) -> dict:
    """95% CIs by resampling PATIENTS with replacement (rows of a patient stay together)."""
    y, s, groups = np.asarray(y), np.asarray(s, dtype=float), np.asarray(groups)
    rng = np.random.default_rng(seed)
    ids = np.unique(groups)
    idx_by = {g: np.where(groups == g)[0] for g in ids}
    res = {"pr_auc": [], "roc_auc": [], "recall": [], "precision": []}
    for _ in range(n_boot):
        idx = np.concatenate([idx_by[g] for g in rng.choice(ids, len(ids))])
        yy, ss = y[idx], s[idx]
        if yy.sum() == 0 or yy.sum() == len(yy):
            continue
        res["pr_auc"].append(average_precision_score(yy, ss))
        res["roc_auc"].append(roc_auc_score(yy, ss))
        b = at_budget(yy, ss, units_per_week, budget)
        res["recall"].append(b["recall"])
        res["precision"].append(b["precision"])
    return {k: [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))] for k, v in res.items()}


def paired_diff_ci(y, s_a, s_b, groups, n_boot: int = 1000, seed: int = 0) -> dict:
    """PR-AUC(a) - PR-AUC(b) with a patient bootstrap; 'better' only if the whole 95% CI is above zero."""
    y, s_a, s_b, groups = map(np.asarray, (y, s_a, s_b, groups))
    rng = np.random.default_rng(seed)
    ids = np.unique(groups)
    idx_by = {g: np.where(groups == g)[0] for g in ids}
    d = []
    for _ in range(n_boot):
        idx = np.concatenate([idx_by[g] for g in rng.choice(ids, len(ids))])
        if 0 < y[idx].sum() < len(idx):
            d.append(average_precision_score(y[idx], s_a[idx]) - average_precision_score(y[idx], s_b[idx]))
    lo, hi = np.percentile(d, [2.5, 97.5])
    return {"diff": float(average_precision_score(y, s_a) - average_precision_score(y, s_b)), "ci95": [float(lo), float(hi)], "better": bool(lo > 0)}
