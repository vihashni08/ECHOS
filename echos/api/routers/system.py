"""System config, data quality, metrics, LLM budget, limitations."""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Query, Request

from echos.api import services as V
from echos.api.models import LimitationsOut, LLMBudgetOut, MetricsOut, QualityOut, SystemConfigOut

from echos.api.deps import ERR, ctx_of

router = APIRouter(prefix="/api", responses=ERR)


@router.get("/system/config", response_model=SystemConfigOut, tags=["system"])
def sys_config(request: Request):
    """Flags the UI needs: whether agent kill is enabled and whether mail is real (never secrets)."""
    ctx = ctx_of(request)
    return {"allow_kill": ctx.allow_kill, "dry_run_mail": os.environ.get("ECHOS_DRY_RUN") == "1" or not ctx.cfg.get("alerts", {}).get("send_real", False),
            "llm_enabled": os.environ.get("ECHOS_REPORT_LLM") != "off", "demo": os.environ.get("ECHOS_DEMO") == "1"}

@router.get("/system/quality", response_model=QualityOut, tags=["system"])
def sys_quality(request: Request):
    return V.quality(ctx_of(request))

@router.get("/system/metrics", response_model=MetricsOut, tags=["system"])
def sys_metrics(request: Request, names: Optional[str] = Query(None, description="comma-separated metrics file names (default model_selection,calibration,fall_audit)")):
    return V.read_metrics(ctx_of(request), [n for n in names.split(",") if n] if names else None)

@router.get("/system/llm-budget", response_model=LLMBudgetOut, tags=["system"])
def sys_llm(request: Request):
    return V.llm_budget(ctx_of(request))

@router.get("/system/limitations", response_model=LimitationsOut, tags=["system"])
def sys_limits(request: Request):
    return V.limitations(ctx_of(request))
