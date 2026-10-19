"""Daily and weekly reports, sources, weekly build/send."""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import select

from echos.api import services as V
from echos.api.models import DailyReportOut, SourceOut, WeeklyReportOut, WeeklySendIn, WeeklySendOut
from echos.orchestrator import state as S

from echos.api.deps import ERR, ctx_of

router = APIRouter(prefix="/api", responses=ERR)


@router.get("/patients/{pid}/reports/daily", response_model=DailyReportOut, tags=["reports"])
def daily(request: Request, pid: str, day: Optional[str] = None):
    return V.daily_report(ctx_of(request), pid, day)

@router.get("/patients/{pid}/reports/weekly", response_model=WeeklyReportOut, tags=["reports"])
def weekly(request: Request, pid: str, week: Optional[str] = Query(None, description="ISO week, e.g. 2019-W19")):
    return V.weekly_report(ctx_of(request), pid, week)

@router.get("/patients/{pid}/reports/weekly-available", response_model=list[str], tags=["reports"])
def weekly_available(request: Request, pid: str):
    """ISO weeks with a stored weekly report for this patient (the UI builds a missing one instead of probing for a 404)."""
    return V.weekly_weeks(ctx_of(request), pid)

@router.get("/patients/{pid}/reports/weekly/pdf", tags=["reports"], response_class=FileResponse)
def weekly_pdf(request: Request, pid: str, week: Optional[str] = None):
    ctx = ctx_of(request)
    wk, _, _, row = V.weekly_row(ctx, pid, week)
    if row is None or not V._inside(ctx, row.path).exists():
        raise V.NotFound(f"no weekly PDF for {pid} in {wk}")
    return FileResponse(V._inside(ctx, row.path), media_type="application/pdf", filename=f"{pid}_{wk}.pdf")

@router.post("/patients/{pid}/reports/weekly/build", response_model=WeeklyReportOut, tags=["reports"])
def weekly_build(request: Request, pid: str, week: Optional[str] = None):
    """Build (or rebuild) the weekly PDF and store it; nothing is emailed."""
    ctx = ctx_of(request)
    V.require_patient(ctx, pid)
    wk, _, _ = V.week_end_of(week, ctx, pid)
    from echos.reports.weekly import weekly_report as WR
    kw = {"budget": None, "retriever": None, "root": ctx.report_root, **{k: v for k, v in {"pdy": ctx.pdy, "phys": ctx.phys, "demo": ctx.demo}.items() if v is not None}, **ctx.weekly_kwargs}
    if kw.get("retriever") is None:
        from echos.reports.daily.agent import ReportAgent
        kw["retriever"] = ReportAgent(ctx.eng, None, root=ctx.report_root).retriever
    WR.make_weekly(ctx.eng, pid, wk, None, send=False, **kw)
    return V.weekly_report(ctx, pid, wk)

@router.get("/sources/{sid}", response_model=SourceOut, tags=["reports"])
def source(request: Request, sid: str):
    return V.source_detail(ctx_of(request), sid)

@router.post("/patients/{pid}/reports/weekly/send", response_model=WeeklySendOut, tags=["reports"])
def weekly_send(request: Request, pid: str, body: WeeklySendIn = WeeklySendIn(), week: Optional[str] = None):
    """Build (or rebuild) the weekly PDF and email it to the CLINICIAN. Respects alerts.send_real (false = outbox only, DryRun).
    Without `force` a week that was already sent is not sent again."""
    ctx = ctx_of(request)
    V.require_patient(ctx, pid)
    wk, _, _ = V.week_end_of(week, ctx, pid)
    from echos.alerts.dispatcher import AlertDispatcher
    from echos.alerts.mailer import make_mailer
    from echos.reports.weekly import weekly_report as WR
    with ctx.lock:
        if ctx.dispatcher is None:
            ctx.dispatcher = AlertDispatcher(ctx.eng, make_mailer(ctx.cfg))
    kw = {"budget": "auto", "retriever": None, "root": ctx.report_root, **{k: v for k, v in {"pdy": ctx.pdy, "phys": ctx.phys, "demo": ctx.demo}.items() if v is not None}, **ctx.weekly_kwargs}
    res = WR.send_weekly_now(pid, wk, force=body.force, eng=ctx.eng, dispatcher=ctx.dispatcher, **kw)
    ctx.dispatcher.flush(5)
    aid = res["queued_alert_id"]
    with ctx.eng.begin() as c:
        rows = c.execute(select(S.outbox).where(S.outbox.c.alert_id.like(f"weekly|{pid}|{wk}%")).order_by(S.outbox.c.outbox_id.desc())).fetchall()
    return {"patient_id": pid, "week": wk, "queued": aid is not None, "deduplicated": aid is None, "alert_id": aid,
            "send_real": bool(ctx.cfg.get("alerts", {}).get("send_real", False)) and os.environ.get("ECHOS_DRY_RUN") != "1", "mode": res["mode"],
            "outbox": [V.outbox_out(x) for x in rows]}
