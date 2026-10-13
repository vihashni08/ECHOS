"""Alert lifecycle in the database: NEW -> SENT -> ACKNOWLEDGED -> RESOLVED, or ESCALATED when nobody acknowledged in time.
Pure DB functions shared by the Care Coordinator agent (runtime) and the API (ack / resolve buttons): the API never loads models or agents."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select

from echos.orchestrator import state as S

OPEN_STATES = ("NEW", "SENT", "ESCALATED")


def get(eng, alert_id: str):
    with eng.begin() as c:
        return c.execute(select(S.alert_state).where(S.alert_state.c.alert_id == alert_id)).first()


def acknowledge(eng, alert_id: str, by: str = "caregiver", now: Optional[float] = None) -> Optional[str]:
    """Idempotent. Returns the new state, the existing state if it was already acknowledged / resolved, or None if the alert is unknown."""
    row = get(eng, alert_id)
    if row is None:
        return None
    if row.state in ("ACKNOWLEDGED", "RESOLVED"):
        return row.state
    with eng.begin() as c:
        c.execute(S.alert_state.update().where(S.alert_state.c.alert_id == alert_id).values(state="ACKNOWLEDGED", ack_ts=now or S.now(), ack_by=by[:40]))
    return "ACKNOWLEDGED"


def resolve(eng, alert_id: str, note: str = "", now: Optional[float] = None) -> Optional[str]:
    row = get(eng, alert_id)
    if row is None:
        return None
    if row.state == "RESOLVED":
        return row.state
    with eng.begin() as c:
        c.execute(S.alert_state.update().where(S.alert_state.c.alert_id == alert_id).values(
            state="RESOLVED", resolved_ts=now or S.now(), ack_ts=row.ack_ts or now or S.now(), note=(note or row.note or "")[:300]))
        c.execute(S.care_task.update().where((S.care_task.c.alert_id == alert_id) & (S.care_task.c.status == "OPEN")).values(status="DONE", done_ts=S.now()))
    return "RESOLVED"
