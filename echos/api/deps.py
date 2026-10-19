"""Shared FastAPI pieces for the routers."""
from fastapi import Query, Request

from echos.api import services as V
from echos.api.models import ErrorOut

ERR = {404: {"model": ErrorOut}, 409: {"model": ErrorOut}, 422: {"model": ErrorOut}, 403: {"model": ErrorOut}}
Limit, Offset = Query(50, ge=1, le=500), Query(0, ge=0)


def ctx_of(request: Request) -> V.Ctx:
    return request.app.state.ctx
