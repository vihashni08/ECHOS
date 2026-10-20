"""Agent activity: ONE time-ordered view of what every detector, agent and infrastructure part did, assembled from the tables they already write
(agent_output, case_state, agent_message, alerts, outbox, alert_state, care_task, case_file, reports, restart_log). Read-only; nothing here runs an agent.
Every event: {ts (wall clock), agent, action, text, patient_id, day, ref}. Detector rows are many, so they appear only when asked for (agent filter) or for one case."""
from __future__ import annotations

import json
import time
from typing import Optional

from sqlalchemy import func, select

from echos.api.services import Ctx
from echos.api import team_services as T
from echos.orchestrator import state as S

DETECTORS = ("quality", "health_monitoring", "fall")
ORDER = ("quality", "health_monitoring", "fall", "triage", "planner", "dispatcher", "investigator", "coordinator", "report", "supervisor", "watchdog")
NAMES = {"quality": "Data Quality gate", "health_monitoring": "Vitals detector", "fall": "Fall detector", "triage": "Triage agent", "planner": "Team Planner", "dispatcher": "Alert dispatcher",
         "investigator": "Case Investigator", "coordinator": "Care Coordinator", "report": "Report agent", "supervisor": "Supervisor", "watchdog": "Watchdog"}
ALERT = {"fall": "possible fall", "critical_vitals": "critical vitals reading"}


def _j(x, default=None):
    try:
        return json.loads(x) if x else default
    except (TypeError, ValueError):
        return default


def _ev(ts, agent, action, text, pid="", day="", ref=None):
    return {"ts": float(ts or 0), "agent": agent, "action": action, "text": text, "patient_id": pid or "", "day": day or "", "ref": ref or ""}


def _detector_text(agent: str, res: dict) -> tuple[str, str]:
    if agent == "fall":
        s = res.get("score")
        return ("flagged" if res.get("flag") else "checked"), (f"fall check: score {s:.2f}" + (", flagged" if res.get("flag") else ", not flagged") if isinstance(s, (int, float)) else "fall check")
    if agent == "quality":
        ev = res.get("evidence") or {}
        gap = ev.get("sensor_gap")
        return ("flagged" if gap else "checked"), ("data check: " + ("sensor gap" if gap else "data usable") + ("" if ev.get("has_vitals", True) else ", no vitals reading"))
    v = res.get("vitals") or {}
    parts = ["vitals " + ("flagged" if v.get("flag") else "as usual")] if v.get("available") else []
    return ("flagged" if v.get("flag") else "checked"), ("health check: " + ", ".join(parts) if parts else "health check: nothing to score")


def events(ctx: Ctx, limit: int = 100, agent: Optional[str] = None, patient_id: Optional[str] = None, day: Optional[str] = None, asc: bool = False) -> list[dict]:
    one_case = bool(patient_id and day)
    want = lambda a: agent is None or agent == a  # noqa: E731
    out: list = []
    flt = lambda t: [t.c.patient_id == patient_id] if patient_id else []  # noqa: E731
    with ctx.eng.begin() as c:
        def rows(q, tbl, day_col=None):
            for f in flt(tbl):
                q = q.where(f)
            if day and day_col is not None:
                q = q.where(day_col == day)
            return c.execute(q.limit(2000)).fetchall()
        if (agent in DETECTORS) or one_case:
            for r in rows(select(S.agent_output).order_by(S.agent_output.c.done_ts.desc()), S.agent_output, S.agent_output.c.day):
                if agent and r.agent_id != agent:
                    continue
                act, txt = _detector_text(r.agent_id, _j(r.result, {}))
                out.append(_ev(r.done_ts, r.agent_id, act, txt, r.patient_id, r.day, f"event {r.event_id}"))
        if want("triage"):
            # case_state is an upsert: its timestamp is the LAST update (after the report), not the decision. The decision came before everything it caused, so use the
            # earliest of: the update, the first hand-over Triage posted for that day, and the first alert queued for that day.
            first: dict = {}
            for m in c.execute(select(S.agent_message.c.patient_id, S.agent_message.c.day, func.min(S.agent_message.c.ts)).where(S.agent_message.c.sender == "triage").group_by(S.agent_message.c.patient_id, S.agent_message.c.day)):
                first[(m[0], m[1])] = m[2]
            for m in c.execute(select(S.alerts.c.patient_id, S.alerts.c.event_time, func.min(S.alerts.c.time)).group_by(S.alerts.c.patient_id, S.alerts.c.event_time)):
                k = (m[0], (m[1] or "")[:10])
                first[k] = min(first.get(k, m[2]), m[2])
            for r in rows(select(S.case_state).order_by(S.case_state.c.updated_ts.desc()), S.case_state, S.case_state.c.day):
                why = (_j(r.reasons, []) or [])[:2]
                out.append(_ev(min(r.updated_ts or 0, first.get((r.patient_id, r.day), r.updated_ts or 0)) - 0.001, "triage", "decided", f"decided {r.status}{' (day still open)' if r.provisional else ''}" + (": " + "; ".join(str(x) for x in why) if why else ""), r.patient_id, r.day))
        for r in rows(select(S.agent_message).order_by(S.agent_message.c.id.desc()), S.agent_message, S.agent_message.c.day):
            if agent and r.sender != agent:
                continue
            if r.topic == "team_plan":
                out.append(_ev(r.ts, r.sender, "planned", "planned the team: " + str(_j(r.payload, {}).get("summary", "")), r.patient_id, r.day, f"message {r.id}"))
                continue
            out.append(_ev(r.ts, r.sender, "told", f"told {NAMES.get(r.recipient, r.recipient)}: {r.topic.replace('_', ' ')}", r.patient_id, r.day, f"message {r.id}"))
        if want("dispatcher"):
            for r in rows(select(S.alerts).order_by(S.alerts.c.time.desc()), S.alerts, None):
                if day and (r.event_time or "")[:10] != day:
                    continue
                out.append(_ev(r.time, "dispatcher", "queued", f"queued a {ALERT.get(r.type, r.type)} alert for the caregiver and the clinician", r.patient_id, (r.event_time or "")[:10], r.alert_id))
            ob_q = select(S.outbox, S.alerts.c.patient_id, S.alerts.c.event_time).select_from(S.outbox.join(S.alerts, S.alerts.c.alert_id == S.outbox.c.alert_id)).order_by(S.outbox.c.outbox_id.desc())
            if patient_id:
                ob_q = ob_q.where(S.alerts.c.patient_id == patient_id)
            for r in c.execute(ob_q.limit(2000)):
                d = (r.event_time or "")[:10]
                if day and d != day:
                    continue
                if r.sent_time:
                    out.append(_ev(r.sent_time, "dispatcher", "sent", f"{'dry run (nothing emailed)' if r.channel == 'dry_run' else 'sent'} to the {r.recipient_role}", r.patient_id, d, r.alert_id))
                elif r.status == "FAILED":
                    out.append(_ev(r.queued_time, "dispatcher", "failed", f"could not deliver to the {r.recipient_role}: {str(r.error or 'error')[:60]}", r.patient_id, d, r.alert_id))
        if want("coordinator"):
            for r in rows(select(S.alert_state).order_by(S.alert_state.c.created_ts.desc()), S.alert_state, None):
                if day and r.alert_id.split("|")[1:2] != [day]:
                    continue
                d = r.alert_id.split("|")[1] if "|" in r.alert_id else ""
                for ts, act, txt in ((r.created_ts, "registered", "took ownership of the alert"), (max(r.sent_ts or 0, r.created_ts or 0) if r.sent_ts else None, "waiting", "alert delivered: waiting for an acknowledgement"),
                                     (r.ack_ts, "acknowledged", f"alert acknowledged by {r.ack_by or 'someone'}"), (r.escalated_ts, "escalated", "nobody acknowledged in time: sent to the second contact"),
                                     (r.resolved_ts, "resolved", "alert marked as handled")):
                    if ts:
                        out.append(_ev(ts, "coordinator", act, txt, r.patient_id, d, r.alert_id))
                if r.state == "EXPIRED":
                    out.append(_ev(r.created_ts, "coordinator", "expired", "alert too old to act on: recorded as history, nobody paged", r.patient_id, d, r.alert_id))
            for r in rows(select(S.care_task).order_by(S.care_task.c.created_ts.desc()), S.care_task, S.care_task.c.day):
                out.append(_ev(r.created_ts, "coordinator", "opened task", f"opened a task: {r.title}", r.patient_id, r.day, f"task {r.id}"))
        if want("investigator"):
            for r in rows(select(S.case_file).order_by(S.case_file.c.created_ts.desc()), S.case_file, S.case_file.c.day):
                f = _j(r.file, {})
                steps = len(f.get("trace") or [])
                out.append(_ev(r.created_ts, "investigator", "built case file", f"built a case file ({str(f.get('evidence_level', '?')).lower()} evidence, {steps} step{'' if steps == 1 else 's'}): {str(f.get('headline', ''))[:110]}", r.patient_id, r.day))
        if want("report"):
            for r in rows(select(S.reports).order_by(S.reports.c.created_ts.desc()), S.reports, S.reports.c.day):
                out.append(_ev(r.created_ts, "report", "wrote report", f"wrote the {r.kind} report ({r.mode} wording)", r.patient_id, r.day))
        if (want("supervisor") or want("watchdog")) and not patient_id:
            for r in c.execute(select(S.restart_log).order_by(S.restart_log.c.id.desc()).limit(50)):
                a = "watchdog" if r.agent_id == "runtime" else "supervisor"
                if want(a):
                    out.append(_ev(r.restarted_ts or r.detected_ts, a, "restarted", f"restarted {r.agent_id} ({r.reason})"))
    out.sort(key=lambda e: (e["ts"], ORDER.index(e["agent"]) if e["agent"] in ORDER else 99), reverse=not asc)
    return out[:limit]


def summary(ctx: Ctx) -> list[dict]:
    """Per component: live status (as /components) + how many actions it has logged and its latest one."""
    comps = T.components(ctx)
    ev = events(ctx, limit=100000)
    for d in ("quality", "health_monitoring", "fall"):          # detector rows are counted too (not listed in the default timeline)
        ev += events(ctx, limit=100000, agent=d)
    by: dict = {}
    for e in ev:
        by.setdefault(e["agent"], []).append(e)
    out = []
    for cmp in comps:
        mine = by.get(cmp["id"], [])
        last = max(mine, key=lambda e: e["ts"]) if mine else None
        out.append({"id": cmp["id"], "name": cmp["name"], "kind": cmp["kind"], "status": cmp["status"], "detail": cmp["detail"], "heartbeat_age_s": cmp["heartbeat_age_s"],
                    "actions": len(mine), "last_ts": last["ts"] if last else None, "last_text": last["text"] if last else None})
    extra = by.get("dispatcher", [])
    last = max(extra, key=lambda e: e["ts"]) if extra else None
    out.insert(next((i for i, x in enumerate(out) if x["id"] == "investigator"), len(out)), {"id": "dispatcher", "name": NAMES["dispatcher"], "kind": "infra", "status": "running" if extra else "idle",
               "detail": "queues and sends alert e-mails (fixed template, no LLM)", "heartbeat_age_s": None, "actions": len(extra), "last_ts": last["ts"] if last else None, "last_text": last["text"] if last else None})
    return out
