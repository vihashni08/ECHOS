"""Replay simulation control and the SSE event feed."""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from echos.api import services as V
from echos.api.models import SimControlIn, SimStateOut
from echos.orchestrator import state as S

from echos.api.deps import ERR, ctx_of

router = APIRouter(prefix="/api", responses=ERR)


@router.get("/sim/state", response_model=SimStateOut, tags=["simulation"])
def sim_state(request: Request):
    return V.sim_state(ctx_of(request))

@router.post("/sim/control", response_model=SimStateOut, tags=["simulation"])
def sim_control(request: Request, body: SimControlIn):
    ctx = ctx_of(request)
    if body.action == "speed" and body.speed is None:
        raise ValueError("action 'speed' needs a 'speed' value (events per second)")
    cid, st, res = V.send_command(ctx, body.action, {"speed": body.speed} if body.action == "speed" else {}, wait_s=3.0)
    if st == "ERROR":
        raise HTTPException(409, res or "command failed")
    return V.sim_state(ctx)

@router.get("/events", tags=["simulation"], response_class=StreamingResponse)
async def events(request: Request, last_event_id: Optional[int] = Query(None, description="same as the Last-Event-ID header"),
                 max_seconds: Optional[float] = Query(None, description="close the stream after this many seconds (tests)"),
                 header_id: Optional[str] = Header(None, alias="Last-Event-ID")):
    """Server-Sent Events: alert, day_status, report_ready, agent_health, outbox_status. Comment heartbeat every 15 s. Reconnect with Last-Event-ID."""
    ctx = ctx_of(request)
    start = last_event_id if last_event_id is not None else (int(header_id) if header_id and header_id.isdigit() else None)

    def fetch(after: Optional[int]):
        with ctx.eng.begin() as c:
            if after is None:
                return [], c.execute(select(func.coalesce(func.max(S.event_feed.c.id), 0))).scalar()
            rows = c.execute(select(S.event_feed).where(S.event_feed.c.id > after).order_by(S.event_feed.c.id).limit(200)).fetchall()
        return rows, (rows[-1].id if rows else after)

    def health_transitions():
        """alive/dead changes cannot be a trigger (staleness): the first stream to notice writes the feed row."""
        with ctx.lock:
            for h in V.agent_health(ctx):
                prev = ctx.health_state.get(h["agent"])
                ctx.health_state[h["agent"]] = h["alive"]
                if prev is not None and prev != h["alive"]:
                    with ctx.eng.begin() as c:
                        c.execute(S.event_feed.insert().values(ts=time.time(), kind="agent_health", data=json.dumps({"agent": h["agent"], "status": h["status"], "alive": h["alive"]})))

    async def gen():
        _, last = await run_in_threadpool(fetch, start)
        if start is not None:
            last = start
        t0 = t_hb = t_health = time.monotonic()
        yield "retry: 2000\n: connected\n\n"
        while not await request.is_disconnected():
            if max_seconds is not None and time.monotonic() - t0 > max_seconds:
                return
            if time.monotonic() - t_health > 2:
                t_health = time.monotonic()
                await run_in_threadpool(health_transitions)
            rows, last = await run_in_threadpool(fetch, last)
            for e in rows:
                yield f"id: {e.id}\nevent: {e.kind}\ndata: {e.data}\n\n"
                t_hb = time.monotonic()
            if time.monotonic() - t_hb > 15:
                t_hb = time.monotonic()
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.25)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
