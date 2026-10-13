"""Do the MONITOR thresholds generalise to patients they were not chosen on? Offline, no training, no network.
Thresholds are chosen on 4/5 of the PATIENTS (patient-level folds, repeated with 5 seeds) and applied to the held-out 1/5, using the out-of-fold scores
(models/oof_scores.parquet). Two selection rules are compared on the held-out patients:
  budget : the shipped rule  (vitals <= 1 alert / patient-week; agitation <= 10% of patient-days)
  load   : the vitals threshold that keeps the MONITOR share (vitals OR agitation OR data gap) at or below 25% of patient-days
MONITOR share here excludes possible-fall days (they need the fall agent); it is therefore a LOWER bound of the replay share.
Writes reports/metrics/threshold_holdout.json. Usage: PYTHONPATH=. python scripts/threshold_holdout.py"""
from __future__ import annotations

import json
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
from echos.config import ROOT, load_config  # noqa: E402
from echos.data.patient_day import load_patient_day  # noqa: E402

CAP, SEEDS, K = 0.25, 5, 5


def main():
    cfg = load_config()
    T = cfg["triage"]
    pdy = load_patient_day()
    pdy["day"] = pd.to_datetime(pdy["day"])
    oof = pd.read_parquet(ROOT / "models/oof_scores.parquet")
    oof["day"] = pd.to_datetime(oof["day"])
    v = oof[oof.agent == "physiological"].set_index(["patient_id", "day"]).oof.rename("v")
    a = oof[oof.agent == "behaviour"].set_index(["patient_id", "day"]).oof.rename("a")
    d = pdy.set_index(["patient_id", "day"]).join(v).join(a).reset_index()
    d["gap"] = d.sensor_gap.fillna(True).astype(bool) | (d.days_since_last_reading > T["max_vitals_gap_days"])
    d["yv"] = ((d.bp_alert + d.pulse_alert + d.water_alert) > 0) & d.v.notna()
    pats = np.array(sorted(d.patient_id.unique()))
    grid = np.round(np.arange(0.15, 0.80, 0.01), 2)

    def share(df, vt, at):
        return float(((df.v.fillna(-1) >= vt) | (df.a.fillna(-1) >= at) | df.gap).mean())

    def recall(df, vt):
        y = df[df.yv]
        return float((y.v >= vt).mean()) if len(y) else np.nan

    def budget_thresholds(tr):
        pw = len(tr) / 7.0
        vt = float(np.sort(tr.v.dropna().values)[::-1][max(int(pw) - 1, 0)])
        at = float(np.quantile(tr.a.dropna().values, 0.90))
        return vt, at

    def load_thresholds(tr, at):
        return next((float(g) for g in grid if share(tr, g, at) <= CAP), float(grid[-1]))

    rows = []
    for seed in range(SEEDS):
        rng = np.random.RandomState(seed)
        perm = rng.permutation(pats)
        for f in range(K):
            te_p = set(perm[f::K])
            tr, te = d[~d.patient_id.isin(te_p)], d[d.patient_id.isin(te_p)]
            vb, ab = budget_thresholds(tr)
            vl = load_thresholds(tr, ab)
            rows.append({"seed": seed, "fold": f, "n_test_days": len(te), "v_budget": vb, "a": ab, "v_load": vl,
                         "share_budget": share(te, vb, ab), "share_load": share(te, vl, ab), "recall_budget": recall(te, vb), "recall_load": recall(te, vl),
                         "share_shipped": share(te, T["vitals_prob"], T["agitation_prob"]), "recall_shipped": recall(te, T["vitals_prob"])})
    r = pd.DataFrame(rows)
    full_load = load_thresholds(d, T["agitation_prob"])

    def boot(col, n=2000):
        rng = np.random.RandomState(0)
        by = r.groupby("fold")[col].mean().values
        bs = [rng.choice(by, len(by)).mean() for _ in range(n)]
        return [float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))]
    out = {"patient_days": int(len(d)), "patients": int(len(pats)), "repeats": f"{SEEDS} x {K}-fold patient-level",
           "shipped": {"vitals_prob": T["vitals_prob"], "agitation_prob": T["agitation_prob"], "share_all_days": share(d, T["vitals_prob"], T["agitation_prob"]),
                       "heldout_share_mean": float(r.share_shipped.mean()), "heldout_vitals_recall": float(r.recall_shipped.mean())},
           "budget_rule": {"heldout_share_mean": float(r.share_budget.mean()), "heldout_share_ci95": boot("share_budget"), "heldout_vitals_recall": float(r.recall_budget.mean()),
                           "vitals_prob_chosen_range": [float(r.v_budget.min()), float(r.v_budget.max())]},
           "load_rule_25pct": {"heldout_share_mean": float(r.share_load.mean()), "heldout_share_ci95": boot("share_load"), "heldout_vitals_recall": float(r.recall_load.mean()),
                               "vitals_prob_chosen_range": [float(r.v_load.min()), float(r.v_load.max())], "vitals_prob_on_all_data": full_load,
                               "share_all_days_at_that_threshold": share(d, full_load, T["agitation_prob"]), "heldout_share_over_cap_in_folds": int((r.share_load > CAP).sum()), "folds": int(len(r))},
           "notes": ["out-of-fold scores; thresholds chosen on training patients only and scored on held-out patients",
                     "MONITOR share excludes possible-fall days (about 1% of replay days): a lower bound of the replay share",
                     "the replay share (28.8%) also uses 20 patients x ~63 days, so absolute values differ from this all-patient OOF table"]}
    out["drivers_all_days"] = {"data_gap_any": float(d.gap.mean()), "vitals_flag": float((d.v.fillna(-1) >= T["vitals_prob"]).mean()), "behaviour_flag": float((d.a.fillna(-1) >= T["agitation_prob"]).mean()),
                               "data_gap_only": float((d.gap & ~(d.v.fillna(-1) >= T["vitals_prob"]) & ~(d.a.fillna(-1) >= T["agitation_prob"])).mean()),
                               "gap_reasons": {"sensor_gap": float(d.sensor_gap.fillna(True).astype(bool).mean()), "no_vitals_over_max_days": float((d.days_since_last_reading > T["max_vitals_gap_days"]).mean())}}
    (ROOT / "reports/metrics/threshold_holdout.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: out[k] for k in ("shipped", "budget_rule", "load_rule_25pct")}))


if __name__ == "__main__":
    main()
