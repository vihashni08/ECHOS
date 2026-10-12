"""End-to-end systems replay: N TIHM patients over their full period, injected held-out SisFall falls, the whole pipeline (workers ->
LangGraph -> template reports, DryRun mail, no LLM / PubMed / SMTP). Writes reports/metrics/e2e.json (incl. per-day reasons) and keeps the
run's DB in data/e2e_replay/ (git-ignored) for scripts/gate_eval.py and scripts/fall_eval.py.

Usage: PYTHONPATH=. python scripts/e2e.py [n_patients]   (~5 min for 20 patients: run it in the background)
Agents 1-2 return out-of-fold scores (ECHOS_OOF=1, default); falls come from SisFall subjects held out of the replay fall model."""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
import warnings
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import select

warnings.filterwarnings("ignore")
from echos.offline import force_offline  # noqa: E402

force_offline()   # this stage: no Gemini, no PubMed, no real SMTP
from echos.config import ROOT, load_config  # noqa: E402
from echos.orchestrator import state as S  # noqa: E402
from echos.orchestrator.triage import is_urgent_fall  # noqa: E402
from scripts.kill_test import run_pipeline  # noqa: E402

KEEP = ROOT / "data" / "e2e_replay"
OUT = ROOT / "reports" / "metrics" / "e2e.json"


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (float(c - h), float(c + h))


def gap_causes(q: dict | None, health: dict | None, dsv: int, max_gap: int) -> list[str]:
    """Same DATA_GAP conditions as triage() (kept next to the replay so URGENT days also get a cause)."""
    out, qe = [], (q or {}).get("evidence", {})
    if q is None or not qe.get("quality_checked", True):
        out.append("quality unchecked")
    else:
        if qe.get("sensor_gap"):
            out.append("sensor gap")
        if qe.get("days_since_last_reading") is not None:
            dsv = qe["days_since_last_reading"]
    if health is None:
        out.append("monitoring output missing")
    elif health.get("degraded"):
        out.append("monitoring degraded")
    if dsv > max_gap:
        out.append("vitals gap > 7 days")
    return out


def monitor_class(reasons: list[str]) -> str:
    parts = sorted({"vitals" if x.startswith("vitals") else "behaviour" if x.startswith("agitation") else
                    "possible fall (no inactivity)" if x.startswith("possible fall") else "data gap" if x.startswith("DATA_GAP") else "other"
                    for x in reasons})
    return " + ".join(parts) + (" only" if len(parts) == 1 else "")


def main(n=20):
    os.environ.setdefault("ECHOS_OOF", "1")
    shutil.rmtree(KEEP, ignore_errors=True)
    KEEP.mkdir(parents=True)
    t0 = time.time()
    r = run_pipeline(KEEP, None, n_patients=n, max_days=None, rate=0.0, report_llm=None)   # template reports, DryRun mail
    eng = S.get_engine(KEEP / "echos.db")
    cfg = load_config()
    t, max_gap = cfg["triage"], cfg["triage"]["max_vitals_gap_days"]
    with eng.begin() as c:
        cases = c.execute(select(S.case_state)).fetchall()
        alerts = c.execute(select(S.alerts)).fetchall()
        gt = c.execute(select(S.gt_fall)).fetchall()
        outs = c.execute(select(S.agent_output)).fetchall()
    wall = time.time() - t0
    by_day: dict = {}
    for o in outs:
        by_day.setdefault((o.patient_id, o.day), {}).setdefault(o.agent_id, []).append(json.loads(o.result))
    pw = len(cases) / 7
    gt_ids = {g.event_id: g for g in gt}
    gt_keys = {(g.patient_id, g.day) for g in gt}
    fall_alert = {(a.patient_id, a.alert_id.split("|")[1]): a for a in alerts if a.type == "fall"}
    crit_days = {(a.patient_id, a.alert_id.split("|")[1]) for a in alerts if a.type == "critical_vitals"}
    detected = [k for k in gt_keys if k in fall_alert]
    gts = {(g.patient_id, g.day): g.ts for g in gt}
    delay_h = [(pd.Timestamp(k[1]) + pd.Timedelta(seconds=86399) - pd.Timestamp(gts[k])).total_seconds() / 3600 for k in detected]
    false_fall = [k for k in fall_alert if k not in gt_keys]
    # ---- event-level fall statistics (fall agent outputs): effect of inactivity confirmation
    inj, adl = [], []
    fp_conf, fp_noconf = set(), set()
    for o in outs:
        if o.agent_id != "fall":
            continue
        res = json.loads(o.result)
        urgent, p_ok = is_urgent_fall(res, t), res["score"] >= t["fall_prob"]
        (inj if o.event_id in gt_ids else adl).append({"urgent": urgent, "p_ok": p_ok})
        if o.event_id not in gt_ids:
            if urgent:
                fp_conf.add((o.patient_id, o.day))
            if p_ok:
                fp_noconf.add((o.patient_id, o.day))
    inactivity = {
        "injected_events": len(inj), "injected_p_ge_thr": sum(x["p_ok"] for x in inj), "injected_urgent_with_confirmation": sum(x["urgent"] for x in inj),
        "adl_events": len(adl), "adl_events_p_ge_thr": sum(x["p_ok"] for x in adl), "adl_events_urgent_with_confirmation": sum(x["urgent"] for x in adl),
        "false_fall_alert_days_with_confirmation": len(fp_conf), "false_fall_alert_days_without_confirmation": len(fp_noconf),
        "false_fall_alerts_per_patient_week_with_confirmation": round(len(fp_conf) / pw, 3),
        "false_fall_alerts_per_patient_week_without_confirmation": round(len(fp_noconf) / pw, 3)}
    # ---- per-day reasons
    days, mon_break, gap_cause, st = [], Counter(), Counter(), Counter()
    mismatches = 0
    for c in cases:
        rs = json.loads(c.reasons or "[]")
        d = by_day.get((c.patient_id, c.day), {})
        q = d.get("quality", [None])[-1]
        h = d.get("health_monitoring", [None])[-1]
        gc = gap_causes(q, h, c.days_since_vitals, max_gap)
        mismatches += bool(c.data_gap) != bool(gc)
        st[c.status] += 1
        if c.status == "MONITOR":
            mon_break[monitor_class(rs)] += 1
        for g in gc:
            gap_cause[g] += 1
        days.append({"patient_id": c.patient_id, "day": c.day, "status": c.status, "data_gap": bool(c.data_gap), "gap_causes": gc, "reasons": rs,
                     "injected_fall": (c.patient_id, c.day) in gt_keys})
    n_urgent_gap = sum(1 for x in days if x["status"] == "URGENT" and x["data_gap"])
    gap_only = sum(1 for x in days if x["status"] == "MONITOR" and x["data_gap"])
    nd = len(cases)
    res = {
        "patients": n, "patient_days": nd, "patient_weeks": round(pw, 1), "wall_s": round(wall),
        "injected_falls": len(gt_keys), "falls_detected_as_urgent": len(detected), "fall_detection_rate": round(len(detected) / max(1, len(gt_keys)), 3),
        "falls_detected_pct_ci95_wilson": [round(x, 3) for x in wilson(len(detected), len(gt_keys))],
        "missed_injected_falls": [{"patient_id": g.patient_id, "day": g.day, "subject": g.subject, "trial": Path(g.file).stem}
                                  for g in gt if (g.patient_id, g.day) not in fall_alert],
        "detection_delay_sim_hours_median": round(float(np.median(delay_h)), 1) if delay_h else None,
        "detection_delay_sim_hours_max": round(float(np.max(delay_h)), 1) if delay_h else None,
        "urgent_alerts_total": len(alerts), "urgent_fall_alerts": len(fall_alert), "false_fall_alerts": len(false_fall),
        "false_fall_alerts_per_patient_week": round(len(false_fall) / pw, 3),
        "urgent_critical_vitals_days": len(crit_days), "urgent_alerts_per_patient_week": round(len(alerts) / pw, 3),
        "inactivity_confirmation": inactivity,
        "status_days": dict(st), "status_share": {k: round(v / nd, 3) for k, v in st.items()},
        "day_status_exclusive": {"NORMAL": st["NORMAL"], "MONITOR (alerts, no data gap)": st["MONITOR"] - gap_only,
                                 "DATA_GAP (MONITOR days with a data gap)": gap_only, "URGENT": st["URGENT"]},
        "data_gap_share_any_status": round(sum(x["data_gap"] for x in days) / nd, 3), "urgent_days_with_data_gap": n_urgent_gap,
        "monitor_days_by_reason": dict(mon_break.most_common()), "data_gap_days_by_cause": dict(gap_cause.most_common()),
        "data_gap_flag_vs_reconstruction_mismatches": mismatches,
        "duplicate_alerts": len(r["alerts"]) - len(set(r["alerts"])), "oof_scores": os.environ["ECHOS_OOF"] == "1",
        "mail": "DryRun (outbox only)", "report_mode": "template (no LLM, no retrieval)",
        "thresholds": {k: t[k] for k in ("vitals_prob", "agitation_prob", "fall_prob")},
        "notes": ["detection delay is simulated time from the fall to the end of that patient-day (orchestrator is a daily batch)",
                  "false fall alerts = fall-reason URGENT alerts on days without an injected fall (ADL windows from held-out SisFall subjects)",
                  "critical-vitals URGENT days come from real TIHM readings and are not counted as false",
                  "a day can carry several data-gap causes, so the cause counts can exceed the number of gap days",
                  "Agents 1-2 scores are out-of-fold when ECHOS_OOF=1; Agent 3 uses a fall model trained without the held-out SisFall subjects"],
        "days_file": "e2e_days.json (patient-day rows; git-ignored)"}
    OUT.write_text(json.dumps(res, indent=1, default=list))
    OUT.with_name("e2e_days.json").write_text(json.dumps(days, indent=1, default=list))
    print(json.dumps({k: v for k, v in res.items() if k not in ("notes", "days_file", "missed_injected_falls")}, default=list))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 20)
