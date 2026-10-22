"""Multi-agent layer: components, bus messages, care tasks, outbox."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select

from echos.api import services as V
from echos.api import team_services as T
from echos.api import auth as A
from echos.api.models import AgentMessageOut, CareTaskOut, ComponentOut, OutboxOut, Page
from echos.orchestrator import state as S

from echos.api.deps import ERR, Limit, Offset, ctx_of

router = APIRouter(prefix="/api", responses=ERR)


@router.get("/components", response_model=list[ComponentOut], tags=["agents"])
def components(request: Request):
    """Every detector, agent and infrastructure part: what it does, what it must never do, and whether it is running."""
    return T.components(ctx_of(request))

@router.get("/agents/messages", response_model=list[AgentMessageOut], tags=["agents"])
def agent_messages(request: Request, patient_id: Optional[str] = None, limit: int = Query(50, ge=1, le=300)):
    """The message bus: who told whom what (newest first)."""
    return T.messages(ctx_of(request), patient_id, limit)

@router.get("/tasks", response_model=Page[CareTaskOut], tags=["alerts"])
def care_tasks(request: Request, status: Optional[str] = Query(None, description="OPEN or DONE"), limit: int = Limit, offset: int = Offset):
    rows, n = T.tasks(ctx_of(request), status, limit, offset)
    return {"items": rows, "total": n, "limit": limit, "offset": offset}

@router.post("/tasks/{task_id}/done", response_model=CareTaskOut, tags=["alerts"])
def care_task_done(request: Request, task_id: int):
    return T.task_done(ctx_of(request), task_id)

@router.get("/outbox", response_model=Page[OutboxOut], tags=["alerts"])
def outbox(request: Request, status: Optional[str] = None, limit: int = Limit, offset: int = Offset):
    rows, n = V.list_outbox(ctx_of(request), status, limit, offset)
    return {"items": rows, "total": n, "limit": limit, "offset": offset}

@router.post("/outbox/{outbox_id}/retry", response_model=OutboxOut, tags=["alerts"])
def outbox_retry(request: Request, outbox_id: int):
    """Resend ONE outbox row now (uses this API's mailer: DryRun unless alerts.send_real). A row that is already SENT is never re-sent (409)."""
    ctx = ctx_of(request)
    from echos.alerts.dispatcher import AlertDispatcher
    from echos.alerts.mailer import make_mailer
    with ctx.lock:
        if ctx.dispatcher is None:
            ctx.dispatcher = AlertDispatcher(ctx.eng, make_mailer(ctx.cfg))
    try:
        ctx.dispatcher.retry_row(outbox_id)
    except LookupError as ex:
        raise V.NotFound(str(ex)) from ex
    except ValueError as ex:
        raise HTTPException(409, str(ex)) from ex
    ctx.dispatcher.flush(5)
    with ctx.eng.begin() as c:
        row = c.execute(select(S.outbox).where(S.outbox.c.outbox_id == outbox_id)).first()
    return V.outbox_out(row)
