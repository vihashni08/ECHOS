"""Immediate alerts: the orchestrator's `immediate_alert` node calls queue_alerts() BEFORE any report work. It builds the
structured payload (echos/alerts/payload.py) and hands it to the dispatcher (background thread; fixed template, no LLM, no RAG,
no PubMed: nothing in this path can be taken down by those). One alert per fall event (unique per event_id); critical vitals
once per day-event with a per-patient cooldown (see AlertDispatcher)."""
from __future__ import annotations

import json
import logging

from sqlalchemy import select

from echos.alerts import payload as P
from echos.alerts.dispatcher import AlertDispatcher
from echos.config import load_config
from echos.orchestrator import state as S
from echos.orchestrator.triage import is_urgent_fall

log = logging.getLogger("echos.alerts")


def alert_id(patient_id: str, day: str, type_: str, event_id) -> str:
    return f"{patient_id}|{day}|{type_}|{event_id if event_id is not None else 'na'}"


def _fall_events(eng, patient_id: str, day: str, results: dict, t: dict) -> list:
    """(event_id, ts, fall result) for every fall event of the day that meets the URGENT rule."""
    with eng.begin() as c:
        rows = c.execute(select(S.agent_output.c.event_id, S.agent_output.c.result).where(
            (S.agent_output.c.agent_id == "fall") & (S.agent_output.c.patient_id == patient_id) & (S.agent_output.c.day == day))
            .order_by(S.agent_output.c.event_id)).fetchall()
    out = []
    for eid, res in rows:
        r = json.loads(res)
        if is_urgent_fall(r, t):
            out.append((eid, str(r.get("evidence", {}).get("ts") or f"{day}T23:59:59"), r))
    if not out and results.get("fall") and is_urgent_fall(results["fall"], t):   # inline / test mode: no stored outputs
        f = results["fall"]
        out.append((f.get("evidence", {}).get("event_id"), str(f.get("evidence", {}).get("ts") or f"{day}T23:59:59"), f))
    return out


def _day_event(eng, patient_id: str, day: str):
    with eng.begin() as c:
        r = c.execute(select(S.event_log.c.event_id, S.event_log.c.ts).where(
            (S.event_log.c.patient_id == patient_id) & (S.event_log.c.day == day) & (S.event_log.c.kind == "day"))).first()
    return (r[0], r[1]) if r else (None, f"{day}T23:59:59")


def queue_alerts(eng, dispatcher: AlertDispatcher, patient_id: str, day: str, decision: dict, results: dict) -> int:
    """Returns the number of NEW alerts queued (duplicates / cooldown-suppressed ones are not counted)."""
    t, n = load_config()["triage"], 0
    types = list(dict.fromkeys(decision.get("urgent_types") or []))
    jobs = []
    if "fall" in types:
        jobs += [("FALL", eid, ts, r) for eid, ts, r in _fall_events(eng, patient_id, day, results, t)]
    if "critical_vitals" in types:
        eid, ts = _day_event(eng, patient_id, day)
        jobs.append(("CRITICAL_VITALS", eid, ts, None))
    for typ, eid, ts, fall in jobs:
        aid = alert_id(patient_id, day, typ.lower(), eid)
        try:
            p = P.build_payload(eng, aid, typ, patient_id, day, ts, eid, decision, results, fall)
        except Exception:  # noqa: BLE001 - last resort: the alert still goes out with what is known
            log.exception("payload build failed for %s; sending minimal payload", aid)
            p = P.AlertPayload(alert_id=aid, alert_type=typ, patient_id=patient_id, event_time=ts, event_id=eid,
                               data_quality_note="context unavailable")
        n += bool(dispatcher.dispatch(p))
    return n
