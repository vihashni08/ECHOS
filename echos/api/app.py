"""ECHOS API (FastAPI). DEMO ONLY: no authentication, bound to 127.0.0.1, CORS for the local UI (http://localhost:5173) only.
This process NEVER loads xgboost, torch or any model: it reads the SQLite store, the saved reports and reports/metrics/*.json, and sends commands
(simulation control, fall injection) to the runtime (echos.sim.controller) through the `sim_command` table. Events come from the `event_feed` table.
Run: uvicorn echos.api.app:app --host 127.0.0.1 --port 8000   (docs at /docs). No endpoint returns a secret or a full e-mail address."""
from __future__ import annotations

import json
import os
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from echos.api import services as V
from echos.api import auth as A
from echos.api import ingest_limits as IL
from echos.api.routers import patients, reports, alerts, session, live, team, simulation, agents, system
from echos.config import load_config
from echos.orchestrator import state as S

DESCRIPTION = ("Decision-support demo API for ECHOS (not a diagnostic tool). **No authentication**: for local demos only, bound to 127.0.0.1. "
               "Lists are paginated with `limit` and `offset`. Errors have the shape `{\"error\": {\"status\": 404, \"message\": \"...\"}}`.")


def create_app(ctx: Optional[V.Ctx] = None) -> FastAPI:
    cfg = ctx.cfg if ctx else load_config()
    app = FastAPI(title="ECHOS API", version="1.0", description=DESCRIPTION)
    app.state.ctx = ctx or V.Ctx(eng=S.get_engine(os.environ.get("ECHOS_DB")), cfg=cfg, allow_kill=bool(cfg.get("api", {}).get("allow_kill", False)) or os.environ.get("ECHOS_ALLOW_KILL") == "1",
        report_root=os.environ.get("ECHOS_REPORT_ROOT") or None)
    app.add_middleware(CORSMiddleware, allow_origins=[os.environ.get("ECHOS_CORS_ORIGIN") or cfg.get("api", {}).get("cors_origin", "http://localhost:5173")], allow_methods=["GET", "POST"], allow_headers=["*"])

    app.state.ingest_guard = IL.IngestGuard(cfg)
    app.state.auth_on = A.enabled(cfg)
    app.state.limiter = A.LoginLimiter()

    @app.middleware("http")
    async def _auth(request: Request, call_next):
        if app.state.auth_on:
            tok = request.headers.get("authorization", "")
            tok = tok[7:] if tok.lower().startswith("bearer ") else (request.query_params.get("token") if request.url.path == "/api/events" else None)
            role = A.role_of(tok)
            code, msg = A.allowed(role, request.method, request.url.path)
            if code != 200:
                return JSONResponse({"error": {"status": code, "message": msg}}, status_code=code)
            request.state.role = role
        else:
            request.state.role = "operator"
        if request.method == "POST" and request.url.path == "/api/ingest/events":
            try:
                app.state.ingest_guard.check_size(request.headers.get("content-length"))
            except IL.LimitError as e:
                return JSONResponse({"error": {"status": e.status, "message": e.message}}, status_code=e.status)
        return await call_next(request)

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

    app.include_router(patients.router)
    app.include_router(reports.router)
    app.include_router(alerts.router)
    app.include_router(session.router)
    app.include_router(live.router)
    app.include_router(team.router)
    app.include_router(simulation.router)
    app.include_router(agents.router)
    app.include_router(system.router)
    return app


app = create_app() if os.environ.get("ECHOS_API_NO_AUTOSTART") != "1" else None
