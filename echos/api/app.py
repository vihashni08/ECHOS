"""ECHOS API (FastAPI). DEMO ONLY: no authentication, bound to 127.0.0.1, CORS for the local UI (http://localhost:5173) only.
This process NEVER loads xgboost, torch or any model: it reads the SQLite store, the saved reports and reports/metrics/*.json, and sends commands
(simulation control, fall injection) to the runtime (echos.sim.controller) through the `sim_command` table. Events come from the `event_feed` table.
Run: uvicorn echos.api.app:app --host 127.0.0.1 --port 8000   (docs at /docs). No endpoint returns a secret or a full e-mail address."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import time
from typing import Optional

from fastapi import APIRouter, FastAPI, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from sqlalchemy import func, select
from starlette.exceptions import HTTPException as StarletteHTTPException

from echos.api import services as V
from echos.api.models import (AgentHealth, AlertOut, DailyReportOut, DayStatus, ErrorOut, FallDetail, FallItem, KillOut, LimitationsOut, LLMBudgetOut, MetricsOut, OutboxOut,
                              Page, PatientOut, QualityOut, RoutineOut, SimControlIn, SimFallIn, SimFallOut, SimStateOut, SummaryOut, VitalsOut, WeeklyReportOut,
                              WeeklySendIn, WeeklySendOut)
from echos.config import load_config
from echos.orchestrator import state as S

DESCRIPTION = ("Decision-support demo API for ECHOS (not a diagnostic tool). **No authentication**: for local demos only, bound to 127.0.0.1. "
               "Lists are paginated with `limit` and `offset`. Errors have the shape `{\"error\": {\"status\": 404, \"message\": \"...\"}}`.")
ERR = {404: {"model": ErrorOut}, 409: {"model": ErrorOut}, 422: {"model": ErrorOut}, 403: {"model": ErrorOut}}
Limit, Offset = Query(50, ge=1, le=500), Query(0, ge=0)


def ctx_of(request: Request) -> V.Ctx:
    return request.app.state.ctx


def create_app(ctx: Optional[V.Ctx] = None) -> FastAPI:
    cfg = ctx.cfg if ctx else load_config()
    app = FastAPI(title="ECHOS API", version="1.0", description=DESCRIPTION)
    app.state.ctx = ctx or V.Ctx(eng=S.get_engine(os.environ.get("ECHOS_DB")), cfg=cfg, allow_kill=bool(cfg.get("api", {}).get("allow_kill", False)))
    app.add_middleware(CORSMiddleware, allow_origins=[cfg.get("api", {}).get("cors_origin", "http://localhost:5173")], allow_methods=["GET", "POST"], allow_headers=["*"])

    def err(status: int, msg: str) -> JSONResponse:
        return JSONResponse({"error": {"status": status, "message": msg}}, status_code=status)

    @app.exception_handler(V.NotFound)
    async def _nf(_, ex):
        return err(404, str(ex))

    @app.exception_handler(ValueError)
    async def _ve(_, ex):
        return err(422, str(ex))

    @app.exception_handler(StarletteHTTPException)
    async def _http(_, ex):
        return err(ex.status_code, str(ex.detail))

    @app.exception_handler(RequestValidationError)
    async def _val(_, ex):
        return err(422, "; ".join(f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in ex.errors())[:300])

    r = APIRouter(prefix="/api", responses=ERR)

    # ------------------------------------------------------------ patients
    @r.get("/patients", response_model=Page[PatientOut], tags=["patients"])
    def patients(request: Request, limit: int = Limit, offset: int = Offset):
        rows, n = V.page(V.list_patients(ctx_of(request)), limit, offset)
        return {"items": rows, "total": n, "limit": limit, "offset": offset}

    @r.get("/patients/{pid}/summary", response_model=SummaryOut, tags=["patients"])
    def summary(request: Request, pid: str, day: Optional[str] = None):
        return V.summary(ctx_of(request), pid, day)

    @r.get("/patients/{pid}/statuses", response_model=list[DayStatus], tags=["patients"])
    def statuses(request: Request, pid: str, days: int = Query(7, ge=1, le=120)):
        return V.statuses(ctx_of(request), pid, days)

    @r.get("/patients/{pid}/vitals", response_model=VitalsOut, tags=["patients"])
    def vitals(request: Request, pid: str, from_: Optional[str] = Query(None, alias="from"), to: Optional[str] = None):
        return V.vitals(ctx_of(request), pid, from_, to)

    @r.get("/patients/{pid}/routine", response_model=RoutineOut, tags=["patients"])
    def routine(request: Request, pid: str, from_: Optional[str] = Query(None, alias="from"), to: Optional[str] = None):
        return V.routine(ctx_of(request), pid, from_, to)

    # ------------------------------------------------------------ reports
    @r.get("/patients/{pid}/reports/daily", response_model=DailyReportOut, tags=["reports"])
    def daily(request: Request, pid: str, day: Optional[str] = None):
        return V.daily_report(ctx_of(request), pid, day)

    @r.get("/patients/{pid}/reports/weekly", response_model=WeeklyReportOut, tags=["reports"])
    def weekly(request: Request, pid: str, week: Optional[str] = Query(None, description="ISO week, e.g. 2019-W19")):
        return V.weekly_report(ctx_of(request), pid, week)

    @r.get("/patients/{pid}/reports/weekly/pdf", tags=["reports"], response_class=FileResponse)
    def weekly_pdf(request: Request, pid: str, week: Optional[str] = None):
        ctx = ctx_of(request)
        wk, _, _, row = V.weekly_row(ctx, pid, week)
        if row is None or not V._inside(ctx, row.path).exists():
            raise V.NotFound(f"no weekly PDF for {pid} in {wk}")
        return FileResponse(V._inside(ctx, row.path), media_type="application/pdf", filename=f"{pid}_{wk}.pdf")

    @r.post("/patients/{pid}/reports/weekly/send", response_model=WeeklySendOut, tags=["reports"])
    def weekly_send(request: Request, pid: str, body: WeeklySendIn = WeeklySendIn(), week: Optional[str] = None):
        """Build (or rebuild) the weekly PDF and email it to the CLINICIAN. Respects alerts.send_real (false = outbox only, DryRun).
        Without `force` a week that was already sent is not sent again."""
        ctx = ctx_of(request)
        V.require_patient(ctx, pid)
        wk, _, _ = V.week_end_of(week, ctx, pid)
        from echos.alerts.dispatcher import AlertDispatcher
        from echos.alerts.mailer import make_mailer
        from echos.reports import weekly_report as WR
        with ctx.lock:
            if ctx.dispatcher is None:
                ctx.dispatcher = AlertDispatcher(ctx.eng, make_mailer(ctx.cfg))
        kw = {"budget": "auto", "retriever": None, "root": ctx.root, **{k: v for k, v in {"pdy": ctx.pdy, "phys": ctx.phys, "demo": ctx.demo}.items() if v is not None}, **ctx.weekly_kwargs}
        res = WR.send_weekly_now(pid, wk, force=body.force, eng=ctx.eng, dispatcher=ctx.dispatcher, **kw)
        ctx.dispatcher.flush(5)
        aid = res["queued_alert_id"]
        with ctx.eng.begin() as c:
            rows = c.execute(select(S.outbox).where(S.outbox.c.alert_id.like(f"weekly|{pid}|{wk}%")).order_by(S.outbox.c.outbox_id.desc())).fetchall()
        return {"patient_id": pid, "week": wk, "queued": aid is not None, "deduplicated": aid is None, "alert_id": aid,
                "send_real": bool(ctx.cfg.get("alerts", {}).get("send_real", False)) and os.environ.get("ECHOS_DRY_RUN") != "1", "mode": res["mode"],
                "outbox": [V.outbox_out(x) for x in rows]}

    # ------------------------------------------------------------ alerts, outbox, falls
    @r.get("/alerts", response_model=Page[AlertOut], tags=["alerts"])
    def alerts(request: Request, patient_id: Optional[str] = None, limit: int = Limit, offset: int = Offset):
        rows, n = V.list_alerts(ctx_of(request), patient_id, limit, offset)
        return {"items": rows, "total": n, "limit": limit, "offset": offset}

    @r.get("/outbox", response_model=Page[OutboxOut], tags=["alerts"])
    def outbox(request: Request, status: Optional[str] = None, limit: int = Limit, offset: int = Offset):
        rows, n = V.list_outbox(ctx_of(request), status, limit, offset)
        return {"items": rows, "total": n, "limit": limit, "offset": offset}

    @r.get("/falls", response_model=Page[FallItem], tags=["falls"])
    def falls(request: Request, limit: int = Limit, offset: int = Offset):
        rows, n = V.list_falls(ctx_of(request), limit, offset)
        return {"items": rows, "total": n, "limit": limit, "offset": offset}

    @r.get("/falls/{fid}", response_model=FallDetail, tags=["falls"])
    def fall(request: Request, fid: int):
        return V.fall_detail(ctx_of(request), fid)

    # ------------------------------------------------------------ simulation
    @r.post("/sim/fall", response_model=SimFallOut, tags=["simulation"])
    def sim_fall(request: Request, body: SimFallIn):
        """Inject a SisFall fall window for a patient at the current simulated time. The response states which SisFall subject it came from and whether
        that subject was in the training set of the model used by the Fall worker (the replay model excludes the held-out subjects) and of the final model."""
        ctx = ctx_of(request)
        V.require_patient(ctx, body.patient_id)
        w = V.pick_fall_window(ctx, body.subject)
        hold = set(ctx.cfg["replay"]["holdout_subjects"])
        cid, st, res = V.send_command(ctx, "inject_fall", {"patient_id": body.patient_id, **w}, wait_s=5.0)
        if st == "ERROR":
            raise HTTPException(409, res or "injection failed")
        out = json.loads(res) if st == "DONE" and res else {}
        return {"command_id": cid, "status": "DONE" if st == "DONE" else "QUEUED", "patient_id": body.patient_id, "day": out.get("day"), "event_id": out.get("event_id"),
                "sisfall_subject": w["subject"], "activity": w["activity"], "file": os.path.basename(w["file"]),
                "in_training_set_of_worker_model": w["subject"] not in hold, "in_training_set_of_final_model": True,
                "note": ("SYNTHETIC: a SisFall recording (young volunteers on mats) injected into a real TIHM patient-day. The Fall worker's model excludes the held-out subjects; "
                         "the final model (models/fall_v2.joblib) was trained on all subjects, so it has seen this subject. The patient's current day closes now so the alert is raised now."
                         + ("" if st == "DONE" else " The runtime has not acknowledged the command yet (is it running?)."))}

    @r.get("/sim/state", response_model=SimStateOut, tags=["simulation"])
    def sim_state(request: Request):
        return V.sim_state(ctx_of(request))

    @r.post("/sim/control", response_model=SimStateOut, tags=["simulation"])
    def sim_control(request: Request, body: SimControlIn):
        ctx = ctx_of(request)
        if body.action == "speed" and body.speed is None:
            raise ValueError("action 'speed' needs a 'speed' value (events per second)")
        cid, st, res = V.send_command(ctx, body.action, {"speed": body.speed} if body.action == "speed" else {}, wait_s=3.0)
        if st == "ERROR":
            raise HTTPException(409, res or "command failed")
        return V.sim_state(ctx)

    @r.get("/events", tags=["simulation"], response_class=StreamingResponse)
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

    # ------------------------------------------------------------ agents
    @r.get("/agents/health", response_model=list[AgentHealth], tags=["agents"])
    def agents_health(request: Request):
        return V.agent_health(ctx_of(request))

    @r.post("/agents/{name}/kill", response_model=KillOut, tags=["agents"])
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

    # ------------------------------------------------------------ system
    @r.get("/system/quality", response_model=QualityOut, tags=["system"])
    def sys_quality(request: Request):
        return V.quality(ctx_of(request))

    @r.get("/system/metrics", response_model=MetricsOut, tags=["system"])
    def sys_metrics(request: Request, names: Optional[str] = Query(None, description="comma-separated metrics file names (default model_selection,calibration,fall_audit)")):
        return V.read_metrics(ctx_of(request), [n for n in names.split(",") if n] if names else None)

    @r.get("/system/llm-budget", response_model=LLMBudgetOut, tags=["system"])
    def sys_llm(request: Request):
        return V.llm_budget(ctx_of(request))

    @r.get("/system/limitations", response_model=LimitationsOut, tags=["system"])
    def sys_limits(request: Request):
        return V.limitations(ctx_of(request))

    app.include_router(r)
    return app


app = create_app() if os.environ.get("ECHOS_API_NO_AUTOSTART") != "1" else None
