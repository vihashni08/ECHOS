"""End-to-end systems run: replay N TIHM patients over their full period with injected SisFall falls through the
whole pipeline (workers -> LangGraph -> reports). Writes reports/metrics/e2e.json.

Usage: PYTHONPATH=. python3 scripts/e2e.py [n_patients]
Agents 1-2 return their out-of-fold scores (ECHOS_OOF=1, models/oof_scores.parquet from
scripts/choose_monitor_thresholds.py) so the Normal/Monitor/Urgent shares are not inflated by in-sample fits;
set ECHOS_OOF=0 to replay with the final in-sample models. Falls come from SisFall subjects held out of the fall model.
"""
from __future__ import annotations

import json
import os
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
    os.environ.setdefault("ECHOS_OOF", "1")
    work = Path(tempfile.mkdtemp())
    t0 = time.time()
    try:
        r = run_pipeline(work, None, n_patients=n, max_days=None, rate=0.0, report_llm=None)  # template reports: no LLM/API calls on 1000+ patient-days
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
    mon_break, gap_cause = Counter(), Counter()
    for c in cases:
        rs = json.loads(c.reasons or "[]")
        if c.status == "MONITOR":
            parts = sorted({"vitals" if x.startswith("vitals alert") else "behaviour" if x.startswith("agitation") else
                            "possible_fall" if x.startswith("possible fall") else "DATA_GAP" if x.startswith("DATA_GAP") else "other"
                            for x in rs})
            mon_break[" + ".join(parts) + (" only" if len(parts) == 1 else "")] += 1
        if c.data_gap:
            if c.status != "MONITOR":
                gap_cause["URGENT day (cause not recorded in reasons)"] += 1
                continue
            for x in (g[len("DATA_GAP:"):].strip() for g in rs if g.startswith("DATA_GAP")):
                gap_cause["sensor gap" if x.startswith("sensor gap") else "vitals gap > 7 days" if x.startswith("no vitals reading")
                          else "quality unchecked" if x.startswith("quality") else "agent output missing"] += 1
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
        "monitor_days_by_reason": dict(mon_break.most_common()), "data_gap_days_by_cause_monitor_days": dict(gap_cause.most_common()),
        "duplicate_alerts": r["alerts"].__len__() - len(set(r["alerts"])),
        "notes": ["detection delay is simulated time from the fall to the end of that patient-day (orchestrator is daily-batch); "
                  "wall latency is measured after the day's agent outputs exist",
                  "false fall alerts = fall-reason URGENT alerts on days without an injected fall (ADL windows from held-out SisFall subjects)",
                  "critical-vitals URGENT days come from real TIHM readings and are not counted as false",
                  "Agents 1-2 scores are out-of-fold when ECHOS_OOF=1 (default); Agent 3 uses a fall model trained without the held-out SisFall subjects"],
        "oof_scores": os.environ["ECHOS_OOF"] == "1"}
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
