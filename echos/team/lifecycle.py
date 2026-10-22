"""Alert lifecycle in the database: NEW -> SENT -> ACKNOWLEDGED -> RESOLVED, or ESCALATED when nobody acknowledged in time.
Pure DB functions shared by the Care Coordinator agent (runtime) and the API (ack / resolve buttons): the API never loads models or agents."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select

from echos.orchestrator import state as S

OPEN_STATES = ("NEW", "SENT", "ESCALATED")      # EXPIRED = too old to act on (history after a restart)


def get(eng, alert_id: str):
    with eng.begin() as c:
        return c.execute(select(S.alert_state).where(S.alert_state.c.alert_id == alert_id)).first()


def _register_if_queued(eng, alert_id: str):
    """A person may acknowledge an alert in the second before the Care agent has registered it: register it here (state NEW) so the
    acknowledgement is never refused. None when no such alert was ever queued."""
    with eng.begin() as c:
        a = c.execute(select(S.alerts.c.patient_id, S.alerts.c.type).where(S.alerts.c.alert_id == alert_id)).first()
        if a is None:
            return None
        S.insert_ignore(c, S.alert_state, alert_id=alert_id, patient_id=a.patient_id, type=(a.type or "").upper(), state="NEW", created_ts=S.now())
    return get(eng, alert_id)


def acknowledge(eng, alert_id: str, by: str = "caregiver", now: Optional[float] = None) -> Optional[str]:
    """Idempotent. Returns the new state, the existing state if it was already acknowledged / resolved, or None if the alert is unknown."""
    row = get(eng, alert_id) or _register_if_queued(eng, alert_id)
    if row is None:
        return None
    if row.state in ("ACKNOWLEDGED", "RESOLVED"):
        return row.state
    with eng.begin() as c:
        c.execute(S.alert_state.update().where(S.alert_state.c.alert_id == alert_id).values(state="ACKNOWLEDGED", ack_ts=now or S.now(), ack_by=by[:40]))
    return "ACKNOWLEDGED"


def resolve(eng, alert_id: str, note: str = "", now: Optional[float] = None) -> Optional[str]:
    row = get(eng, alert_id) or _register_if_queued(eng, alert_id)
    if row is None:
        return None
    if row.state == "RESOLVED":
        return row.state
    with eng.begin() as c:
        c.execute(S.alert_state.update().where(S.alert_state.c.alert_id == alert_id).values(
            state="RESOLVED", resolved_ts=now or S.now(), ack_ts=row.ack_ts or now or S.now(), note=(note or row.note or "")[:300]))
        c.execute(S.care_task.update().where((S.care_task.c.alert_id == alert_id) & (S.care_task.c.status == "OPEN")).values(status="DONE", done_ts=S.now()))
    return "RESOLVED"
