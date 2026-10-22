"""System config, evaluation metrics, limitations."""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Query, Request

from echos.api import services as V
from echos.api.models import LimitationsOut, MetricsOut, SystemConfigOut

from echos.api.deps import ERR, ctx_of

router = APIRouter(prefix="/api", responses=ERR)


@router.get("/system/config", response_model=SystemConfigOut, tags=["system"])
def sys_config(request: Request):
    """Flags the UI needs: whether agent kill is enabled and whether mail is real (never secrets)."""
    ctx = ctx_of(request)
    return {"allow_kill": ctx.allow_kill, "dry_run_mail": os.environ.get("ECHOS_DRY_RUN") == "1" or not ctx.cfg.get("alerts", {}).get("send_real", False),
            "llm_enabled": False, "demo": os.environ.get("ECHOS_DEMO") == "1"}

@router.get("/system/metrics", response_model=MetricsOut, tags=["system"])
def sys_metrics(request: Request, names: Optional[str] = Query(None, description="comma-separated metrics file names (default: the v2_* evaluation files)")):
    return V.read_metrics(ctx_of(request), [n for n in names.split(",") if n] if names else None)

@router.get("/system/limitations", response_model=LimitationsOut, tags=["system"])
def sys_limits(request: Request):
    return V.limitations(ctx_of(request))
