"""API helpers for the multi-agent layer: components, alert lifecycle, care tasks, agent messages. Reads / writes SQLite only
(the API never loads agents or models); acknowledgement is a DB write the Care Coordinator reads on its next pass."""
from __future__ import annotations

import json
import time
from typing import Optional

from sqlalchemy import func, select

from echos.api.services import Ctx, NotFound
from echos.components import COMPONENTS
from echos.orchestrator import state as S
from echos.team import lifecycle


def components(ctx: Ctx) -> list[dict]:
    hb_s = ctx.cfg["runtime"]["heartbeat_s"]
    now = time.time()
    with ctx.eng.begin() as c:
        hbs = {r.agent_id: r for r in c.execute(select(S.agent_heartbeat))}
        open_alerts = c.execute(select(func.count()).select_from(S.alert_state).where(S.alert_state.c.state.in_(lifecycle.OPEN_STATES))).scalar()
        tasks = c.execute(select(func.count()).select_from(S.care_task).where(S.care_task.c.status == "OPEN")).scalar()
        scored = c.execute(select(func.count()).select_from(S.agent_output).where(S.agent_output.c.agent_id == "patient_monitor")).scalar()
        reviews = c.execute(select(func.count()).select_from(S.review_list)).scalar()
        msgs = c.execute(select(func.count()).select_from(S.agent_message)).scalar()
    detail = {"patient_monitor": f"{scored} patient-day(s) scored", "review_coordinator": f"{reviews} review(s) assigned",
              "care": f"{open_alerts} open alert(s), {tasks} open task(s)", "bus": f"{msgs} message(s)"}
    out = []
    for comp in COMPONENTS:
        d = {k: comp[k] for k in ("id", "name", "kind", "role", "inputs", "outputs", "on_failure", "never")}
        h = hbs.get(comp["id"])
        age = None if h is None else max(0.0, now - h.ts)
        alive = bool(h and h.status != "stopped" and age < 3 * hb_s + 10)
        status = ("running" if alive else ("missing" if h is None else "stopped")) if comp["worker"] else "n/a"
        out.append({**d, "status": status, "heartbeat_age_s": None if age is None else round(age, 1), "detail": detail.get(comp["id"])})
    return out


def alert_lifecycle(ctx: Ctx, rows: list[dict]) -> list[dict]:
    ids = [r["alert_id"] for r in rows]
    if not ids:
        return rows
    with ctx.eng.begin() as c:
        st = {r.alert_id: r for r in c.execute(select(S.alert_state).where(S.alert_state.c.alert_id.in_(ids)))}
    for r in rows:
        s = st.get(r["alert_id"])
        if s is not None:
            r.update(state=s.state, sent_ts=s.sent_ts, deadline_ts=s.deadline_ts, ack_ts=s.ack_ts, ack_by=s.ack_by, escalated_ts=s.escalated_ts, resolved_ts=s.resolved_ts, note=s.note)
        else:
            r.update(state="NEW")
    return rows


def ack(ctx: Ctx, alert_id: str, by: str) -> str:
    st = lifecycle.acknowledge(ctx.eng, alert_id, by)
    if st is None:
        raise NotFound(f"unknown alert {alert_id!r} (the Care Coordinator registers an alert within a second of it being queued)")
    return st


def resolve(ctx: Ctx, alert_id: str, note: str) -> str:
    st = lifecycle.resolve(ctx.eng, alert_id, note)
    if st is None:
        raise NotFound(f"unknown alert {alert_id!r}")
    return st


def tasks(ctx: Ctx, status: Optional[str], limit: int, offset: int) -> tuple[list, int]:
    q, cnt = select(S.care_task).order_by(S.care_task.c.created_ts.desc()), select(func.count()).select_from(S.care_task)
    if status:
        q, cnt = q.where(S.care_task.c.status == status), cnt.where(S.care_task.c.status == status)
    with ctx.eng.begin() as c:
        rows = [{k: getattr(r, k) for k in ("id", "patient_id", "day", "kind", "title", "detail", "created_by", "created_ts", "due_ts", "status", "alert_id")} for r in c.execute(q.limit(limit).offset(offset))]
        return rows, c.execute(cnt).scalar()


def task_done(ctx: Ctx, tid: int) -> dict:
    with ctx.eng.begin() as c:
        r = c.execute(select(S.care_task).where(S.care_task.c.id == tid)).first()
        if r is None:
            raise NotFound(f"unknown task {tid}")
        c.execute(S.care_task.update().where(S.care_task.c.id == tid).values(status="DONE", done_ts=time.time()))
        r = c.execute(select(S.care_task).where(S.care_task.c.id == tid)).first()
    return {k: getattr(r, k) for k in ("id", "patient_id", "day", "kind", "title", "detail", "created_by", "created_ts", "due_ts", "status", "alert_id")}


def messages(ctx: Ctx, pid: Optional[str], limit: int) -> list[dict]:
    q = select(S.agent_message).order_by(S.agent_message.c.id.desc()).limit(limit)
    if pid:
        q = q.where(S.agent_message.c.patient_id == pid)
    with ctx.eng.begin() as c:
        rows = c.execute(q).fetchall()
    return [{"id": r.id, "ts": r.ts, "sender": r.sender, "recipient": r.recipient, "topic": r.topic, "patient_id": r.patient_id, "day": r.day,
             "payload": json.loads(r.payload or "{}")} for r in rows]
