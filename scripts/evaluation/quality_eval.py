"""How often do the sensor-silence / stale-vitals rules (echos/monitor/quality.py) fire on the real TIHM data? -> reports/metrics/v2_quality.json

TIHM has no ground truth for sensor failures, so this measures the firing rate only (and whether a flag coincides with a confirmed agitation episode),
not detection accuracy: detection is tested on a synthetic outage in tests/test_quality.py. The rules use the same past-only same-slot history as the agent.

    PYTHONPATH=. python scripts/evaluation/quality_eval.py
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from echos.config import ROOT, load_config
from echos.monitor import agitation as A, cohort, quality as Q

OUT = ROOT / "reports" / "metrics" / "v2_quality.json"


def main() -> dict:
    cfg = load_config()["monitor"]
    c = Q.cfg_of(cfg)
    f = A.attach_labels(A.window_features(__import__("echos.data.loaders", fromlist=["x"]).load_activity()), __import__("echos.data.loaders", fromlist=["x"]).load_labels())
    f = f.sort_values(["patient_id", "slot", "w"])
    g = f.groupby(["patient_id", "slot"])["total"]
    f["usual"] = g.transform(lambda s: s.shift(1).rolling(cfg["agitation"]["baseline_slots"], min_periods=c["min_history_windows"]).median())
    f["silent"] = (f["total"] == 0) & (f["usual"] >= c["min_usual_events"])
    f["day"] = (f["w"] - pd.Timedelta(hours=1)).dt.normalize()
    day = f.groupby(["patient_id", "day"]).agg(silent=("silent", "sum"), windows=("silent", "size")).reset_index()
    weeks = f.groupby("patient_id").w.agg(lambda s: (s.max() - s.min()).days / 7).sum()
    bad_days = day[day.silent >= c["silent_windows_per_day"]]
    # stale vitals: days with no reading more than max_vitals_gap_days after the person's previous reading, inside their first-to-last reading span
    vit = cohort.vitals_cohort()
    stale = 0
    span_days = 0
    for _, v in vit.groupby("patient_id"):
        last = None
        for r in v.itertuples():
            if r.has_reading:
                last = r.day
            elif last is not None and (r.day - last).days > c["max_vitals_gap_days"]:
                stale += 1
        span_days += len(v)
    out = {"rules": c, "windows": int(len(f)), "patients": int(f.patient_id.nunique()), "patient_weeks": round(float(weeks), 1),
           "silent_windows": int(f.silent.sum()), "silent_windows_share": round(float(f.silent.mean()), 4),
           "silent_windows_with_agitation_label": int(f[f.silent].y.sum()),
           "probable_device_problem_days": int(len(bad_days)), "device_problem_days_per_patient_week": round(len(bad_days) / float(weeks), 3),
           "patients_with_a_device_problem_day": int(bad_days.patient_id.nunique()),
           "stale_vitals_days": int(stale), "vitals_cohort_days": int(span_days), "stale_vitals_share": round(stale / max(span_days, 1), 4),
           "note": "firing rate on real TIHM data; no ground truth for sensor failures exists, so sensitivity is only tested on a synthetic outage in tests/test_quality.py"}
    OUT.write_text(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    r = main()
    print({k: v for k, v in r.items() if k not in ("rules", "note")})
