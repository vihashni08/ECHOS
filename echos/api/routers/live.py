"""Live ingestion and the device emulator."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from echos.api import live_services as LV
from echos.api import ingest_limits as IL
from echos.live.schemas import IngestBatch
from echos.api.models import EmulatorCmdIn, EmulatorCmdOut, IngestResultOut, LiveStatusOut

from echos.api.deps import ERR, ctx_of

router = APIRouter(prefix="/api", responses=ERR)


@router.post("/ingest/events", response_model=IngestResultOut, tags=["live"])
def ingest(request: Request, batch: IngestBatch, x_echos_token: Optional[str] = Header(None)):
    """Ingestion contract for ANY source (device, hub, emulator): a batch of activity / vitals / accel / heartbeat events with DATA timestamps.
    Idempotent: an exact repeat counts as a duplicate. If ECHOS_INGEST_TOKEN is set, send it as X-Echos-Token. Limits (config `ingest`): 413 too large / too many events, 411 no Content-Length, 403 unregistered device, 429 rate limit (Retry-After)."""
    try:
        LV.check_token(x_echos_token)
    except PermissionError as ex:
        raise HTTPException(403, str(ex))
    try:
        request.app.state.ingest_guard.check_batch(batch.events)
    except IL.LimitError as e:
        return JSONResponse({"error": {"status": e.status, "message": e.message}}, status_code=e.status, headers={"Retry-After": str(e.retry_after)} if e.retry_after else None)
    return LV.ingest(ctx_of(request), batch)

@router.get("/live/status", response_model=LiveStatusOut, tags=["live"])
def live_status(request: Request):
    """Data mode (replay / emulator / live), emulator state and, per patient, how fresh each stream is."""
    return LV.status(ctx_of(request))

@router.post("/live/emulator", response_model=EmulatorCmdOut, tags=["live"])
def live_emulator(request: Request, body: EmulatorCmdIn):
    """Control the labelled device emulator: load | play | pause | speed | fall."""
    return LV.emulator_command(ctx_of(request), body)
