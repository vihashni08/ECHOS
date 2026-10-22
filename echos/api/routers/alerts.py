"""Alerts (ack / resolve) and the e-mail acknowledgement link."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from echos.api import services as V
from echos.api import team_services as T
from echos.alerts import acklink
from echos.team import lifecycle
from echos.api.models import AlertAckIn, AlertOut, Page

from echos.api.deps import ERR, Limit, Offset, ctx_of

router = APIRouter(prefix="/api", responses=ERR)


@router.get("/alerts", response_model=Page[AlertOut], tags=["alerts"])
def alerts(request: Request, patient_id: Optional[str] = None, limit: int = Limit, offset: int = Offset):
    rows, n = V.list_alerts(ctx_of(request), patient_id, limit, offset)
    return {"items": T.alert_lifecycle(ctx_of(request), rows), "total": n, "limit": limit, "offset": offset}

@router.post("/alerts/{alert_id:path}/ack", response_model=AlertOut, tags=["alerts"])
def alert_ack(request: Request, alert_id: str, body: AlertAckIn = AlertAckIn()):
    """Acknowledge an alert (stops the escalation timer). Idempotent."""
    ctx = ctx_of(request)
    T.ack(ctx, alert_id, request.state.role if request.app.state.auth_on else body.by)      # with auth on, who acknowledged is the logged-in role, not client text
    return T.alert_lifecycle(ctx, [V.alert_by_id(ctx, alert_id)])[0]

@router.get("/ack/{token}", response_class=HTMLResponse, tags=["alerts"], include_in_schema=True)
def ack_by_link(request: Request, token: str):
    """The "I have seen this" link in alert e-mails: signed (HMAC) and expiring, no login. Acknowledges the alert it names (idempotent) and shows a small page
    that says nothing about the person. Forged / malformed tokens all get the same answer."""
    page = lambda code, title, msg: HTMLResponse(f"<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>ECHOS</title>"  # noqa: E731
                                                 f"<body style='font-family:system-ui;max-width:32rem;margin:3rem auto;padding:0 1rem'><h1>{title}</h1><p>{msg}</p></body></html>", status_code=code,
                                                 headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    try:
        alert_id = acklink.verify_token(token)
    except acklink.LinkError as e:
        if e.kind == "expired":
            return page(410, "Link expired", "This link is no longer valid. Open ECHOS and acknowledge the alert there.")
        if e.kind == "unconfigured":
            return page(503, "Not available", "Acknowledging by link is not set up on this server. Open ECHOS and acknowledge the alert there.")
        return page(400, "Link not valid", "This link is not valid. Open ECHOS and acknowledge the alert there.")
    st = lifecycle.acknowledge(ctx_of(request).eng, alert_id, "email link")
    if st is None:
        return page(404, "Alert not found yet", "The alert is not registered yet. Try the link again in a minute, or open ECHOS.")
    return page(200, "Thank you: alert acknowledged", "ECHOS has recorded that you have seen this alert. The escalation timer is stopped. Please still check on the person if you have not.")

@router.post("/alerts/{alert_id:path}/resolve", response_model=AlertOut, tags=["alerts"])
def alert_resolve(request: Request, alert_id: str, body: AlertAckIn = AlertAckIn()):
    """Mark an alert as handled (closes its open tasks)."""
    ctx = ctx_of(request)
    T.resolve(ctx, alert_id, body.note or "")
    return T.alert_lifecycle(ctx, [V.alert_by_id(ctx, alert_id)])[0]
