"""End-to-end check of the v2 runtime with REAL agent processes -> reports/metrics/v2_runtime.json

Starts the controller + supervisor + 3 agent processes on a throw-away database, replays the demo cohort, injects one critical reading,
kills the Patient Monitor process half-way (SIGKILL) and measures: URGENT latency (event written -> alert queued -> e-mail sent, dry-run
mailer), lost / duplicate alerts against a reference run, recovery time, every day allocated, every message consumed.

    PYTHONPATH=. python scripts/evaluation/team_e2e.py [--patients 8] [--days 28] [--no-kill]
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import statistics
import time
from pathlib import Path

from sqlalchemy import func, select

from echos.config import ROOT
from echos.orchestrator import state as S
from echos.team.runtime import TeamRuntime

OUT = ROOT / "reports" / "metrics" / "v2_runtime.json"


def run(db: Path, patients: int, days: int, kill: bool, speed: float = 60.0, timeout: float = 600.0) -> dict:
    for f in db.parent.glob(db.name + "*"):
        f.unlink()
    os.environ["ECHOS_DRY_RUN"] = "1"                                  # dry-run mailer: the outbox is filled, SMTP is never used
    os.environ.setdefault("ECHOS_CAREGIVER_EMAIL", "caregiver@example.test")
    os.environ.setdefault("ECHOS_CLINICIAN_EMAIL", "clinician@example.test")
    rt = TeamRuntime(str(db), patients, days)
    inserted: dict = {}
    orig = rt.ctl._insert

    def rec(e, ts=None, payload=None, gt=None):
        eid = orig(e, ts, payload, gt)
        inserted[eid] = time.time()
        return eid
    rt.ctl._insert = rec
    rt.ctl.speed = speed
    rt.start()
    eng = rt.eng
    t0 = time.time()
    while time.time() - t0 < 60:            # agents up
        with eng.begin() as c:
            n = c.execute(select(func.count()).select_from(S.agent_heartbeat).where(S.agent_heartbeat.c.status == "running")).scalar()
        if n >= 3:
            break
        time.sleep(0.5)
    rt.ctl.handle("play", {})
    killed = None
    injected = None
    total = len(rt.ctl.events)
    while time.time() - t0 < timeout:
        if injected is None and rt.ctl.cursor > total // 4:
            pid = rt.ctl.events[0]["patient_id"] or rt.ctl.events[1]["patient_id"]
            injected = rt.ctl.handle("inject_reading", {"patient_id": pid, "sys": 192, "dia": 104, "hr": 88})
        if kill and killed is None and rt.ctl.cursor > total // 2:
            with eng.begin() as c:
                pid_proc = c.execute(select(S.agent_heartbeat.c.pid).where(S.agent_heartbeat.c.agent_id == "patient_monitor")).scalar()
            os.kill(pid_proc, signal.SIGKILL)
            killed = {"pid": pid_proc, "t": time.time(), "at_event": rt.ctl.cursor}
        if rt.ctl.status == "finished":
            with eng.begin() as c:
                closes = c.execute(select(func.count()).select_from(S.event_log).where(S.event_log.c.kind == "day_close")).scalar()
                ck = c.execute(select(S.agent_checkpoint.c.last_event_id).where(S.agent_checkpoint.c.agent_id == "review_coordinator")).scalar() or 0
                last_close = c.execute(select(func.max(S.event_log.c.event_id)).where(S.event_log.c.kind == "day_close")).scalar() or 0
                new = c.execute(select(func.count()).select_from(S.agent_message).where((S.agent_message.c.status == "NEW") & (S.agent_message.c.recipient != "all"))).scalar()
            if ck >= last_close and new == 0:
                break
        time.sleep(0.25)
    time.sleep(3)  # let the dry-run mailer finish
    with eng.begin() as c:
        alerts = c.execute(select(S.alerts.c.alert_id, S.alerts.c.event_id, S.alerts.c.time)).fetchall()
        kinds = dict(c.execute(select(S.event_log.c.event_id, S.event_log.c.kind)).fetchall())
        sent = {r.alert_id: r.sent_time for r in c.execute(select(S.outbox.c.alert_id, S.outbox.c.sent_time).where(
            (S.outbox.c.recipient_role == "caregiver") & (S.outbox.c.status == "SENT")))}
        restarts = c.execute(select(S.restart_log)).fetchall()
        reviews = c.execute(select(func.count()).select_from(S.review_list)).scalar()
        picks = sorted(f"{r.day}|{r.patient_id}|{r.rank}" for r in c.execute(select(S.review_list)))
        statuses = sorted(f"{r.day}|{r.patient_id}|{r.status}" for r in c.execute(select(S.case_state)))
        review_days = c.execute(select(func.count(func.distinct(S.review_list.c.day)))).scalar()
        status = dict(c.execute(select(S.case_state.c.status, func.count()).group_by(S.case_state.c.status)).fetchall())
        msgs = dict(((r[0], r[1]), r[2]) for r in c.execute(select(S.agent_message.c.sender, S.agent_message.c.topic, func.count()).group_by(
            S.agent_message.c.sender, S.agent_message.c.topic)))
        new_left = c.execute(select(func.count()).select_from(S.agent_message).where((S.agent_message.c.status == "NEW") & (S.agent_message.c.recipient != "all"))).scalar()
        days_scored = c.execute(select(func.count()).select_from(S.agent_output).where(S.agent_output.c.agent_id == "patient_monitor")).scalar()
        hb = {r.agent_id: r.pid for r in c.execute(select(S.agent_heartbeat))}
    rt.close()
    q = [a.time - inserted[a.event_id] for a in alerts if a.event_id in inserted]
    s = [sent[a.alert_id] - inserted[a.event_id] for a in alerts if a.event_id in inserted and sent.get(a.alert_id)]
    out = {"events": total, "days_scored": days_scored, "alerts": len(alerts), "alert_ids": sorted(a.alert_id.rsplit("|", 1)[0] + "|injected" if kinds.get(a.event_id) == "reading" else a.alert_id for a in alerts), "picks": picks, "statuses": statuses,
           "urgent_latency_s": {"event_to_queued_median": statistics.median(q) if q else None, "event_to_queued_max": max(q) if q else None,
                                "event_to_sent_median": statistics.median(s) if s else None, "event_to_sent_max": max(s) if s else None},
           "reviews": reviews, "days_allocated": review_days, "case_status": status, "messages": {f"{k[0]}:{k[1]}": v for k, v in msgs.items()},
           "unconsumed_messages": new_left, "injected_reading": injected, "wall_s": round(time.time() - t0, 1)}
    if killed:
        rs = [r for r in restarts if r.agent_id == "patient_monitor" and r.reason != "sim_reset"]
        out["kill"] = {"agent": "patient_monitor", "at_event": killed["at_event"], "detected_and_restarted": bool(rs),
                       "restart_s": round(rs[0].restarted_ts - killed["t"], 1) if rs else None, "new_pid": hb.get("patient_monitor"),
                       "reason": rs[0].reason if rs else None}
    return out


def main(patients: int, days: int, kill: bool) -> dict:
    tmp = ROOT / "data" / "v2"
    tmp.mkdir(parents=True, exist_ok=True)
    ref = run(tmp / "e2e_ref.db", patients, days, kill=False)
    hide = ("alert_ids", "picks", "statuses")
    res = {"reference": {k: v for k, v in ref.items() if k not in hide}}
    if kill:
        k = run(tmp / "e2e_kill.db", patients, days, kill=True)
        res["kill_run"] = {kk: v for kk, v in k.items() if kk not in hide}
        res["kill_vs_reference"] = {"lost_alerts": len(set(ref["alert_ids"]) - set(k["alert_ids"])),
                                    "extra_alerts": len(set(k["alert_ids"]) - set(ref["alert_ids"])),
                                    "identical_review_picks": ref["picks"] == k["picks"], "identical_case_statuses": ref["statuses"] == k["statuses"],
                                    "same_days_scored": ref["days_scored"] == k["days_scored"]}
    OUT.write_text(json.dumps(res, indent=2, default=str))
    print(json.dumps(res, indent=1, default=str))
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--patients", type=int, default=8)
    ap.add_argument("--days", type=int, default=28)
    ap.add_argument("--no-kill", action="store_true")
    a = ap.parse_args()
    main(a.patients, a.days, not a.no_kill)
