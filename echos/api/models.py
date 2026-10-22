"""Response models of the ECHOS API. Nothing here carries a vitals probability, a full e-mail address or a secret."""
from __future__ import annotations

from typing import Any, Generic, Literal, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class ErrorBody(BaseModel):
    status: int
    message: str


class ErrorOut(BaseModel):
    error: ErrorBody


class PatientOut(BaseModel):
    patient_id: str
    age_band: str
    sex: str
    latest_status: Optional[str] = None
    latest_day: Optional[str] = None
    last_alert_time: Optional[str] = None
    last_alert_type: Optional[str] = None
    days: int = 0                                  # patient-days scored by the Patient Monitor
    reviews: int = 0                               # times on the Review Coordinator's list


class DayStatus(BaseModel):
    day: str
    status: str
    reasons: list[str]


class AlertOut(BaseModel):
    alert_id: str
    patient_id: str
    type: str
    status: str                                    # delivery status: PENDING / SENT / FAILED (from the outbox)
    event_id: Optional[int] = None
    event_time: Optional[str] = None
    time: Optional[float] = None
    state: Optional[str] = None                    # lifecycle (Care Coordinator): NEW / SENT / ACKNOWLEDGED / RESOLVED / ESCALATED
    sent_ts: Optional[float] = None
    deadline_ts: Optional[float] = None            # not acknowledged by then -> escalated
    ack_ts: Optional[float] = None
    ack_by: Optional[str] = None
    escalated_ts: Optional[float] = None
    resolved_ts: Optional[float] = None
    note: Optional[str] = None


class AlertAckIn(BaseModel):
    by: str = "caregiver"
    note: Optional[str] = None


class CareTaskOut(BaseModel):
    id: int
    patient_id: str
    day: Optional[str] = None
    kind: str
    title: str
    detail: Optional[str] = None
    created_by: Optional[str] = None
    created_ts: Optional[float] = None
    due_ts: Optional[float] = None
    status: str
    alert_id: Optional[str] = None


class AgentMessageOut(BaseModel):
    id: int
    ts: float
    sender: str
    recipient: str
    topic: str
    patient_id: Optional[str] = None
    day: Optional[str] = None
    payload: dict = Field(default_factory=dict)


class ComponentOut(BaseModel):
    id: str
    name: str
    kind: str                                      # detector | agent | infra
    role: str
    inputs: str
    outputs: str
    on_failure: str
    never: str
    status: str                                    # running | idle | missing | n/a
    heartbeat_age_s: Optional[float] = None
    detail: Optional[str] = None                   # e.g. "3 open alerts", "last case 2019-05-07"


class OutboxOut(BaseModel):
    outbox_id: int
    alert_id: str
    recipient_role: str
    address: str                                   # masked: ab***@domain
    channel: Optional[str] = None
    subject: Optional[str] = None
    status: str
    attempts: int = 0
    error: Optional[str] = None
    event_time: Optional[str] = None
    queued_time: Optional[float] = None
    sent_time: Optional[float] = None
    attachment_name: Optional[str] = None
    next_attempt_time: Optional[float] = None      # RETRYING / PENDING: when the next automatic attempt is due
    last_attempt_time: Optional[float] = None
    retry_window_end: Optional[float] = None       # alert mails: end of the automatic retry window (alerts.retry_window_s); FAILED rows are then swept every 5 min until sweep_until
    sweep_until: Optional[float] = None
    can_retry: bool = False                        # POST /api/outbox/{id}/retry is possible (not SENT, address configured)
    kind: str = "alert"                            # alert | weekly
    delivery: str = "dry_run"                      # dry_run (nothing leaves the machine) | email (real SMTP)


class SimStateOut(BaseModel):
    status: str
    speed: Optional[float] = None
    cursor: Optional[int] = None
    total: Optional[int] = None
    sim_time: Optional[str] = None
    message: str = ""
    runtime_alive: bool


class SimControlIn(BaseModel):
    action: Literal["play", "pause", "speed", "reset"]
    speed: Optional[float] = Field(None, description="events per second, for action=speed")


class AgentHealth(BaseModel):
    agent: str
    status: str
    alive: bool
    heartbeat_age_s: Optional[float] = None
    last_event_id: Optional[int] = None
    pid: Optional[int] = None
    restarts: int = 0


class KillOut(BaseModel):
    agent: str
    killed_pid: int
    recovered: bool
    recovery_s: Optional[float] = None


class LimitationsOut(BaseModel):
    source: str
    text: str


class MetricsOut(BaseModel):
    available: list[str]
    files: dict[str, Any]


class SystemConfigOut(BaseModel):
    demo: bool = False
    allow_kill: bool
    dry_run_mail: bool
    llm_enabled: bool


class LoginIn(BaseModel):
    token: str = Field(min_length=1, max_length=200)


class AuthOut(BaseModel):
    auth_enabled: bool
    role: Optional[str] = None                     # caregiver | clinician | operator | null (not logged in)


