"""Re-select MONITOR thresholds from out-of-fold predictions under an alert budget.
Vitals: <= 1 alert per patient-week (all patient-days / 7). Behaviour (agitation_prob only; drift is report evidence): <= 10% of patient-days.
Writes models/oof_scores.parquet (used by scripts/e2e.py) and reports/metrics/monitor_thresholds.json; prints a table."""
import json
import numpy as np
import pandas as pd
from echos.agents.health import behaviour as b, vitals as ph
from echos.config import ROOT, load_config
from echos.data.patient_day import load_patient_day
from echos.data.splits import patient_folds

cfg = load_config()
seed, nf, T = cfg["seed"], cfg["splits"]["n_folds"], cfg["triage"]
pdy = load_patient_day()
n_days, pw = len(pdy), len(pdy) / 7.0


CAL = json.loads((ROOT / "reports/metrics/calibration.json").read_text()) if (ROOT / "reports/metrics/calibration.json").exists() else {}


def oof(df, feats):
    if CAL.get("use_calibrated"):  # same alert-budget rule, on the calibrated nested out-of-fold score
        from echos.agents.health import calibration as C
        name = "vitals" if feats is ph.FEATURES else "behaviour"
        mk = ph._model if feats is ph.FEATURES else b._model
        return C.nested_oof(df, feats, mk, seed, nf)[1][CAL[name]["chosen_kind"]]
    y, o = df.y.values, np.zeros(len(df))
    for tr, te in patient_folds(df, nf, seed):
        m = ph._model(seed, (1 - y[tr].mean()) / max(y[tr].mean(), 1e-6)) if feats is ph.FEATURES else \
            b._model(seed, (1 - y[tr].mean()) / max(y[tr].mean(), 1e-6))
        o[te] = m.fit(df.iloc[tr][feats], y[tr]).predict_proba(df.iloc[te][feats])[:, 1]
    return o


d1 = ph.build_features(pdy)
d1["oof"] = oof(d1, ph.FEATURES)
d2 = b.build_features(pdy)
d2 = d2[d2.has_activity & d2.total_events_delta.notna() & d2.total_events_z.notna()].reset_index(drop=True)
d2["oof"] = oof(d2, b.CLF_FEATURES)
for d in (d1, d2):
    d["day"] = pd.to_datetime(d["day"])
pd.concat([d1[["patient_id", "day", "oof"]].assign(agent="physiological"),
           d2[["patient_id", "day", "oof"]].assign(agent="behaviour")]).to_parquet(ROOT / "models" / "oof_scores.parquet")

# ---- vitals: highest-scoring k = pw alerts
k = int(pw)
new_v = float(np.sort(d1.oof.values)[::-1][k - 1])
# ---- behaviour: drift_score gate first (past-only CUSUM, no model), then the agitation threshold under the 10% cap
CAP = 0.10
ds = d2[[f"{f}_cusum" for f in b.FEATS]].max(axis=1).values  # max CUSUM across features (uncapped)


def beh_share(drift_t, agit_t):
    """Share of patient-days that raise a behaviour MONITOR. drift_t=None: agitation_prob is the only trigger."""
    hit = d2.oof.values >= agit_t
    if drift_t is not None:
        hit = hit | (ds >= drift_t)
    return float(hit.sum() / n_days)


old = {"vitals_prob": T["vitals_prob"], "agitation_prob": T["agitation_prob"], "drift_cusum": T.get("drift_cusum", T.get("drift_evidence_cusum"))}
# new rule: agitation_prob alone, at the 10% cap. Drift stays in reports as evidence at its old threshold (not a trigger).
cands = np.sort(np.unique(d2.oof.values))
agit_new = next(t for t in cands if beh_share(None, t) <= CAP)
agit_new = float(round(agit_new + 5e-4, 3))
new = {"vitals_prob": round(new_v, 3), "agitation_prob": agit_new, "drift_evidence_cusum": old["drift_cusum"]}


def stats(v, a, dr, trigger_drift=True):
    yv, ya = d1.y.values, d2.y.values
    return {"vitals_alerts_per_patient_week": float((d1.oof >= v).sum() / pw),
            "vitals_recall": float(yv[d1.oof.values >= v].sum() / yv.sum()),
            "vitals_precision": float(yv[d1.oof.values >= v].mean()),
            "agitation_recall": float(ya[d2.oof.values >= a].sum() / ya.sum()),
            "drift_recall": float(ya[ds >= dr].sum() / ya.sum()),
            "behaviour_monitor_recall": float(ya[((ds >= dr) if trigger_drift else False) | (d2.oof.values >= a)].sum() / ya.sum()),
            "behaviour_monitor_share_of_patient_days": beh_share(dr if trigger_drift else None, a),
            "drift_share_of_patient_days": float((ds >= dr).sum() / n_days)}


_prev = ROOT / "reports/metrics/monitor_thresholds.json"
if CAL.get("use_calibrated") and _prev.exists() and "raw_scale_run" not in json.loads(_prev.read_text()):
    # score scale changed: the old row = the previous script run on the RAW score scale (its stats are not recomputable on the new scale)
    old_block = {**json.loads(_prev.read_text())["new"], "config_vitals_prob": old["vitals_prob"], "config_agitation_prob": old["agitation_prob"]}
else:
    old_block = {**old, **stats(old["vitals_prob"], old["agitation_prob"], old["drift_cusum"])}
res = {"patient_days": n_days, "patient_weeks": pw, "old": old_block, "score_scale": "calibrated" if CAL.get("use_calibrated") else "raw",
       "new": {**new, **stats(new["vitals_prob"], new["agitation_prob"], old["drift_cusum"], trigger_drift=False)},
       "notes": ["OOF scores, patient-level GroupKFold; vitals budget uses all patient-days / 7 (as Agent 1 does)",
                 "old = max CUSUM >= drift_cusum OR agitation_prob (thresholds as in the previous config, re-scored on the cleaned table); new = agitation_prob only, capped at 10% of all patient-days; drift is report evidence only (drift_evidence_cusum)",
                 "agitation recall counts same-day labels on activity days; vitals recall = BP/pulse/water alert labels on reading days"]}
(ROOT / "reports/metrics/monitor_thresholds.json").write_text(json.dumps(res, indent=2))
rows = []
for kk in ["vitals_prob", "agitation_prob", "drift_cusum", "drift_evidence_cusum", "vitals_alerts_per_patient_week", "vitals_recall", "drift_share_of_patient_days",
           "agitation_recall", "behaviour_monitor_recall", "behaviour_monitor_share_of_patient_days"]:
    rows.append((kk, res["old"].get(kk), res["new"].get(kk)))
print(pd.DataFrame(rows, columns=["", "old", "new"]).round(3).to_string(index=False))
