"""Patients and their day statuses."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query, Request

from echos.api import services as V
from echos.api.models import DayStatus, Page, PatientOut

from echos.api.deps import ERR, Limit, Offset, ctx_of

router = APIRouter(prefix="/api", responses=ERR)


@router.get("/patients", response_model=Page[PatientOut], tags=["patients"])
def patients(request: Request, limit: int = Limit, offset: int = Offset):
    rows, n = V.page(V.list_patients(ctx_of(request)), limit, offset)
    return {"items": rows, "total": n, "limit": limit, "offset": offset}

@router.get("/patients/{pid}/statuses", response_model=list[DayStatus], tags=["patients"])
def statuses(request: Request, pid: str, days: int = Query(28, ge=1, le=120)):
    return V.statuses(ctx_of(request), pid, days)
