"""Choose triage thresholds from validation (out-of-fold) results; writes reports/metrics/triage_thresholds.json."""
import json
import numpy as np
import pandas as pd
from echos.agents import fall
from echos.config import ROOT, load_config
from echos.data.splits import subject_folds

cfg = load_config()
df = fall.build_windows().reset_index(drop=True)
y = df.y.values
oof = np.zeros(len(df))
for tr, te in subject_folds(df, 5, cfg["seed"]):
    oof[te] = fall._rf(cfg["seed"]).fit(df.iloc[tr][fall.FEATURES], y[tr]).predict_proba(df.iloc[te][fall.FEATURES])[:, 1]
hrs = df[df.y == 0].groupby("file")["file_seconds"].max().sum() / 3600
rows = []
for t in [0.5, 0.7, 0.8, 0.9, 0.95]:
    for inact in [False, True]:
        a = (oof >= t) & (df.post_inactive.values if inact else True)
        rows.append({"T": t, "needs_inactivity": inact, "sensitivity": float(a[y == 1].mean()),
                     "false_alarms_per_hour": fall._alert_events(df[y == 0], a[y == 0]) / hrs})
out = {"fall_operating_points_oof": rows,
       "agent1_threshold_1_per_patient_week": json.load(open(ROOT / "reports/metrics/agent1.json"))["threshold_at_1_per_week"],
       "agent2_threshold_1_per_patient_week": json.load(open(ROOT / "reports/metrics/agent2.json"))["threshold_at_1_per_week"]}
(ROOT / "reports/metrics/triage_thresholds.json").write_text(json.dumps(out, indent=2))
print(pd.DataFrame(rows).round(3).to_string(index=False)); print({k: v for k, v in out.items() if k.startswith("agent")})
p = pd.read_parquet(cfg["paths"]["processed"]); print("vitals coverage", p.has_reading.mean().round(2), "activity", p.has_activity.mean().round(2))
