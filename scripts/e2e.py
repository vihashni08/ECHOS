"""End-to-end systems run: replay N TIHM patients over their full period with injected SisFall falls through the
whole pipeline (workers -> LangGraph -> reports). Writes reports/metrics/e2e.json.

Usage: PYTHONPATH=. python3 scripts/e2e.py [n_patients]
Caveat: Agents 1-2 were final-fitted on all TIHM patients, so their replayed outputs (and the Normal/Monitor/Urgent
shares) are a systems result, not out-of-sample accuracy. Falls come from SisFall subjects held out of the fall model.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
import warnings
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import select

warnings.filterwarnings("ignore")
from echos.orchestrator import state as S  # noqa: E402
from scripts.kill_test import run_pipeline  # noqa: E402


def main(n=20):
    work = Path(tempfile.mkdtemp())
    t0 = time.time()
    try:
        r = run_pipeline(work, None, n_patients=n, max_days=None, rate=0.0)
        eng = S.get_engine(work / "echos.db")
        with eng.begin() as c:
            cases = c.execute(select(S.case_state)).fetchall()
            alerts = c.execute(select(S.alerts)).fetchall()
            gt = c.execute(select(S.gt_fall)).fetchall()
            outs = c.execute(select(S.agent_output.c.patient_id, S.agent_output.c.day, S.agent_output.c.done_ts)).fetchall()
    finally:
        shutil.rmtree(work, ignore_errors=True)
    wall = time.time() - t0
    pw = len(cases) / 7
    fall_alert = {(a.patient_id, a.day): a for a in alerts if any(x.startswith("fall") for x in json.loads(a.reasons))}
    crit_alert = {(a.patient_id, a.day) for a in alerts if any(x.startswith("critical") for x in json.loads(a.reasons))}
    gt_keys = {(g.patient_id, g.day): g for g in gt}
    detected = [k for k in gt_keys if k in fall_alert]
    delay_h = [(pd.Timestamp(k[1]) + pd.Timedelta(seconds=86399) - pd.Timestamp(gt_keys[k].ts)).total_seconds() / 3600 for k in detected]
    last_out = pd.DataFrame(outs, columns=["p", "d", "t"]).groupby(["p", "d"]).t.max()
    lat = [a.created_ts - last_out[(a.patient_id, a.day)] for a in alerts if (a.patient_id, a.day) in last_out.index]
    st = Counter(c.status for c in cases)
    false_fall = [k for k in fall_alert if k not in gt_keys]
    res = {
        "patients": n, "patient_days": len(cases), "patient_weeks": round(pw, 1), "wall_s": round(wall),
        "injected_falls": len(gt_keys), "falls_detected_as_urgent": len(detected),
        "fall_detection_rate": round(len(detected) / max(1, len(gt_keys)), 3),
        "falls_detected_pct_ci95_wilson": [round(x, 3) for x in _wilson(len(detected), len(gt_keys))],
        "detection_delay_sim_hours_median": round(float(np.median(delay_h)), 1) if delay_h else None,
        "detection_delay_sim_hours_max": round(float(np.max(delay_h)), 1) if delay_h else None,
        "pipeline_latency_s_median_after_last_agent_output": round(float(np.median(lat)), 2) if lat else None,
        "urgent_alerts_total": len(alerts), "urgent_fall_alerts": len(fall_alert),
        "false_fall_alerts": len(false_fall), "false_fall_alerts_per_patient_week": round(len(false_fall) / pw, 3),
        "urgent_critical_vitals_days": len(crit_alert),
        "urgent_alerts_per_patient_week": round(len(alerts) / pw, 3),
        "status_days": dict(st), "status_share": {k: round(v / len(cases), 3) for k, v in st.items()},
        "data_gap_share": round(sum(bool(c.data_gap) for c in cases) / len(cases), 3),
        "duplicate_alerts": r["alerts"].__len__() - len(set(r["alerts"])),
        "notes": ["detection delay is simulated time from the fall to the end of that patient-day (orchestrator is daily-batch); "
                  "wall latency is measured after the day's agent outputs exist",
                  "false fall alerts = fall-reason URGENT alerts on days without an injected fall (ADL windows from held-out SisFall subjects)",
                  "critical-vitals URGENT days come from real TIHM readings and are not counted as false",
                  "Agents 1-2 were fitted on all TIHM patients: status shares are not out-of-sample"]}
    Path("reports/metrics/e2e.json").write_text(json.dumps(res, indent=2))
    print(json.dumps({k: v for k, v in res.items() if k != "notes"}))


def _wilson(k, n, z=1.96):
    if n == 0:
        return (0, 0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 20)
