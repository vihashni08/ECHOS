"""Agent health and the demo kill switch."""
from __future__ import annotations

import os
import signal
import time

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select

from echos.api import services as V
from echos.api.models import AgentHealth, KillOut
from echos.orchestrator import state as S

from echos.api.deps import ERR, ctx_of

router = APIRouter(prefix="/api", responses=ERR)


@router.get("/agents/health", response_model=list[AgentHealth], tags=["agents"])
def agents_health(request: Request):
    return V.agent_health(ctx_of(request))

@router.post("/agents/{name}/kill", response_model=KillOut, tags=["agents"])
def agents_kill(request: Request, name: str, wait_s: float = Query(90, ge=1, le=300)):
    """Fault demo: SIGKILL a worker; the supervisor restarts it. Refused unless config api.allow_kill is true."""
    ctx = ctx_of(request)
    if not ctx.allow_kill:
        raise HTTPException(403, "killing agents is disabled (config api.allow_kill: false)")
    h = next((x for x in V.agent_health(ctx) if x["agent"] == name), None)
    if h is None:
        raise V.NotFound(f"unknown agent {name!r}")
    if not h["pid"]:
        raise HTTPException(409, f"agent {name} has no known process")
    t0 = time.time()
    try:
        os.kill(h["pid"], signal.SIGKILL)
    except ProcessLookupError:
        pass
    while time.time() - t0 < wait_s:
        time.sleep(0.5)
        with ctx.eng.begin() as c:
            hb = c.execute(select(S.agent_heartbeat).where(S.agent_heartbeat.c.agent_id == name)).first()
        if hb and hb.pid != h["pid"] and hb.ts > t0:
            return {"agent": name, "killed_pid": h["pid"], "recovered": True, "recovery_s": round(time.time() - t0, 1)}
    return {"agent": name, "killed_pid": h["pid"], "recovered": False, "recovery_s": None}
