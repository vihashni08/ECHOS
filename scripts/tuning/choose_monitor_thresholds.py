"""Re-select the vitals MONITOR threshold from out-of-fold predictions under an alert budget: <= 1 vitals alert per patient-week (all patient-days / 7).
Writes models/oof_scores.parquet (used by scripts/evaluation/e2e.py) and reports/metrics/monitor_thresholds.json; prints a table."""
import json
import numpy as np
import pandas as pd
from echos.agents.health import vitals as ph
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
        return C.nested_oof(df, feats, ph._model, seed, nf)[1][CAL["vitals"]["chosen_kind"]]
    y, o = df.y.values, np.zeros(len(df))
    for tr, te in patient_folds(df, nf, seed):
        m = ph._model(seed, (1 - y[tr].mean()) / max(y[tr].mean(), 1e-6))
        o[te] = m.fit(df.iloc[tr][feats], y[tr]).predict_proba(df.iloc[te][feats])[:, 1]
    return o


d1 = ph.build_features(pdy)
d1["oof"] = oof(d1, ph.FEATURES)
d1["day"] = pd.to_datetime(d1["day"])
d1[["patient_id", "day", "oof"]].assign(agent="physiological").to_parquet(ROOT / "models" / "oof_scores.parquet")

# ---- vitals: highest-scoring k = pw alerts
k = int(pw)
new_v = float(np.sort(d1.oof.values)[::-1][k - 1])
old = {"vitals_prob": T["vitals_prob"]}


def stats(v):
    yv = d1.y.values
    return {"vitals_alerts_per_patient_week": float((d1.oof >= v).sum() / pw),
            "vitals_recall": float(yv[d1.oof.values >= v].sum() / yv.sum()),
            "vitals_precision": float(yv[d1.oof.values >= v].mean())}


res = {"patient_days": n_days, "patient_weeks": pw, "old": {**old, **stats(old["vitals_prob"])}, "score_scale": "calibrated" if CAL.get("use_calibrated") else "raw",
       "new": {"vitals_prob": round(new_v, 3), **stats(new_v)},
       "notes": ["OOF scores, patient-level GroupKFold; vitals budget uses all patient-days / 7 (as Agent 1 does)",
                 "vitals recall = BP/pulse/water alert labels on reading days"]}
(ROOT / "reports/metrics/monitor_thresholds.json").write_text(json.dumps(res, indent=2))
rows = [(kk, res["old"].get(kk), res["new"].get(kk)) for kk in ["vitals_prob", "vitals_alerts_per_patient_week", "vitals_recall", "vitals_precision"]]
print(pd.DataFrame(rows, columns=["", "old", "new"]).round(3).to_string(index=False))
