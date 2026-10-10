"""Compare behaviour Monitor triggers at a ~10% patient-day budget, using out-of-fold Agent 2 scores only.
(a) max CUSUM >= drift_cusum OR agitation_prob >= agitation_prob (config); (b) agitation_prob alone at the 10% budget;
(c) agitation_prob above the patient's own PAST 90th percentile (expanding, shift(1), >= 14 days history).
Writes reports/metrics/behaviour_triggers.json. Patient-level bootstrap CIs. Does not touch config.yaml."""
import json
import numpy as np
import pandas as pd
from echos.agents import behaviour as b
from echos.config import ROOT, load_config
from echos.data.patient_day import load_patient_day

cfg = load_config()
T, seed = cfg["triage"], cfg["seed"]
pdy = load_patient_day()
n_days = len(pdy)
oof = pd.read_parquet(ROOT / "models/oof_scores.parquet").query("agent == 'behaviour'")
d = b.build_features(pdy)
d["day"] = pd.to_datetime(d["day"])
d = d.merge(oof[["patient_id", "day", "oof"]], on=["patient_id", "day"]).sort_values(["patient_id", "day"]).reset_index(drop=True)
d["cusum_max"] = d[[f"{f}_cusum" for f in b.FEATS]].max(axis=1)
CAP = 0.10


def past_pctl(q):
    def f(g):
        past = g.oof.shift(1)
        thr = past.expanding(min_periods=14).quantile(q)
        return (g.oof > thr).fillna(False)
    return d.groupby("patient_id", group_keys=False).apply(f)


# (b) threshold such that flagged days = 10% of ALL patient-days
k = int(CAP * n_days)
thr_b = float(np.sort(d.oof.values)[::-1][k - 1])
trig = {
    "a_drift_or_agitation": (d.cusum_max >= T["drift_evidence_cusum"]) | (d.oof >= T["agitation_prob"]),
    "b_agitation_alone": d.oof >= thr_b,
    "c_patient_past_p90": past_pctl(0.90),
}
# c' budget-matched: past quantile q chosen (on the pooled OOF data) so the flagged share is as close to 10% as possible
best = min(np.arange(0.80, 0.995, 0.005), key=lambda q: abs(past_pctl(q).sum() / n_days - CAP))
trig[f"c2_patient_past_p{best * 100:.1f}_budget_matched"] = past_pctl(best)

y = d.y.values
pids = d.patient_id.unique()
groups = {p: np.where(d.patient_id.values == p)[0] for p in pids}
rng = np.random.RandomState(seed)
boot_idx = [np.concatenate([groups[p] for p in rng.choice(pids, len(pids))]) for _ in range(1000)]


def recall(flag, idx):
    yy = y[idx]
    return flag[idx][yy == 1].mean() if yy.sum() else np.nan


rows = {}
for name, f in trig.items():
    f = f.values.astype(bool)
    bs = np.array([recall(f, i) for i in boot_idx])
    bs = bs[~np.isnan(bs)]
    sh = np.array([f[i].mean() for i in boot_idx])
    rows[name] = {"share_of_scored_days": float(f.mean()), "share_of_all_patient_days": float(f.sum() / n_days),
                  "recall": float(f[y == 1].mean()), "recall_ci95": [float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))],
                  "flagged_days": int(f.sum()), "agitation_days_caught": int(f[y == 1].sum()),
                  "patients_with_agitation_caught": int(d[f & (y == 1)].patient_id.nunique())}
# random flagging at the same share: expected recall = share
for name, r in rows.items():
    r["random_baseline_recall"] = r["share_of_scored_days"]
res = {"patient_days_all": n_days, "scored_days": len(d), "agitation_days": int(y.sum()),
       "patients_with_agitation": int(d[d.y == 1].patient_id.nunique()), "agitation_thresholds": {"b": thr_b, "config": T["agitation_prob"]},
       "triggers": rows,
       "notes": ["OOF scores only (patient-level GroupKFold); patient bootstrap 1000x on scored days",
                 "thresholds for (a) and (b) were set on the same OOF scores (mild optimism); (c) uses no global threshold",
                 "(c) cannot flag a patient's first 14 scored days; agitation days there count as missed",
                 "random baseline = flagged share (expected recall of flagging days at random)"]}
(ROOT / "reports/metrics/behaviour_triggers.json").write_text(json.dumps(res, indent=2))
t = pd.DataFrame([{"trigger": k, "share_all_days": f"{v['share_of_all_patient_days']:.1%}", "recall": f"{v['recall']:.3f}",
                   "95% CI": f"{v['recall_ci95'][0]:.3f}-{v['recall_ci95'][1]:.3f}", "random": f"{v['random_baseline_recall']:.3f}",
                   "pts_caught": v["patients_with_agitation_caught"]} for k, v in rows.items()])
print(t.to_string(index=False))
print(f"{res['agitation_days']} agitation days, {res['patients_with_agitation']} patients")
