"""Patient summary, statuses and vitals."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query, Request

from echos.api import services as V
from echos.api.models import DayStatus, Page, PatientOut, SummaryOut, VitalsOut

from echos.api.deps import ERR, Limit, Offset, ctx_of

router = APIRouter(prefix="/api", responses=ERR)


@router.get("/patients", response_model=Page[PatientOut], tags=["patients"])
def patients(request: Request, limit: int = Limit, offset: int = Offset):
    rows, n = V.page(V.list_patients(ctx_of(request)), limit, offset)
    return {"items": rows, "total": n, "limit": limit, "offset": offset}

@router.get("/patients/{pid}/summary", response_model=SummaryOut, tags=["patients"])
def summary(request: Request, pid: str, day: Optional[str] = None):
    return V.summary(ctx_of(request), pid, day)

@router.get("/patients/{pid}/statuses", response_model=list[DayStatus], tags=["patients"])
def statuses(request: Request, pid: str, days: int = Query(7, ge=1, le=120)):
    return V.statuses(ctx_of(request), pid, days)

@router.get("/patients/{pid}/vitals", response_model=VitalsOut, tags=["patients"])
def vitals(request: Request, pid: str, from_: Optional[str] = Query(None, alias="from"), to: Optional[str] = None):
    return V.vitals(ctx_of(request), pid, from_, to)

