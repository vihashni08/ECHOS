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


class Completeness(BaseModel):
    days: int = 0
    days_with_vitals: int = 0


class PatientOut(BaseModel):
    patient_id: str
    age_band: str
    sex: str
    latest_status: Optional[str] = None
    latest_day: Optional[str] = None
    last_alert_time: Optional[str] = None
    last_alert_type: Optional[str] = None
    attention_kind: Optional[str] = None           # change | data | None: 'data' = only data problems (a device needs checking, not the person)
    completeness: Completeness


class SummaryOut(BaseModel):
    patient_id: str
    day: str
    status: str
    status_text: str
    reasons: list[str]
    alerts: list[str] = []
    data_note: Optional[str] = None


class DayStatus(BaseModel):
    day: str
    status: str
    reasons: list[str]


class VitalPoint(BaseModel):
    t: str
    value: float
    flagged: bool
    base: Optional[float] = None
    lo: Optional[float] = None
    hi: Optional[float] = None


class DayBand(BaseModel):
    day: str
    lo: float
    hi: float
    base: float


class VitalSeries(BaseModel):
    name: str
    unit: str
    baseline_established: bool
    baseline_note: Optional[str] = None
    points: list[VitalPoint]
    bands: list[DayBand]


class VitalsOut(BaseModel):
    patient_id: str
    start: str
    end: str
    min_history_readings: int
    series: dict[str, VitalSeries]


class Provenance(BaseModel):
    model: str = "template"
    mode: str = "template"
    cache_hit: bool = False
    tools: list[dict[str, Any]] = []
    sources_retrieved: list[dict[str, Any]] = []
    sources_cited: list[str] = []
    validator: Optional[dict[str, Any]] = None
    fallback_reason: Optional[str] = None
    requests: int = 0
    tokens: dict[str, int] = {}
    models_tried: list[str] = []
    events: list[dict[str, Any]] = []
    retrieval_events: list[str] = []
    pubmed_failure: Optional[str] = None


class DailyReportOut(BaseModel):
    patient_id: str
    day: str
    mode: str
    text: str
    status: Optional[str] = None
    alerts: list[str] = []
    changes: list[str] = []
    monitor: list[str] = []
    data_note: Optional[str] = None
    doc: Optional[dict[str, Any]] = None         # structured daily report (echos/reports/daily/document.py)
    created_ts: Optional[float] = None
    validation: Optional[dict[str, Any]] = None
    provenance: Optional[Provenance] = None      # 'How this report was made'


class WeeklyReportOut(BaseModel):
    patient_id: str
    week: str
    week_start: str
    week_end: str
    mode: str
    narrative: Optional[str] = None
    provenance: Optional[dict[str, Any]] = None
    validation: Optional[dict[str, Any]] = None
    pdf_available: bool
    pdf_pages: Optional[int] = None
    data: Optional[dict[str, Any]] = None
    findings: Optional[list[dict[str, Any]]] = None          # finding -> supporting sources: [{key, label, n, sources: [id], source_objs: [...]}]
    considerations: Optional[list[dict[str, Any]]] = None    # [{finding, text, cites}]


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


class CaseFileOut(BaseModel):
    patient_id: str
    day: str
    status: str
    headline: str
    evidence_level: str                            # STRONG | MODERATE | LIMITED (not a probability)
    reasons: list[str] = Field(default_factory=list)
    specifics: list[str] = Field(default_factory=list)       # the recorded values behind the flags, each with the person's own usual and the difference
    caveats: list[str] = Field(default_factory=list)
    next_checks: list[str] = Field(default_factory=list)
    trace: list[dict] = Field(default_factory=list)
    detectors: dict = Field(default_factory=dict)
    history: Optional[dict] = None
    agreement: Optional[dict] = None
    disclaimer: str = ""


class AgentPickOut(BaseModel):
    id: str
    active: bool
    role: str = ""
    reason: str
    need: Optional[float] = None          # the nearest-threshold ratio r
    threshold: Optional[float] = None     # the cutoff theta it had to exceed (None: a fired rule decided it)


class TeamPlanOut(BaseModel):
    """The Team Planner's record for one patient-day: which of the four agents the case needed and why (a ratio and a cutoff, not a model)."""
    patient_id: str
    day: str
    status: str
    team_size: int
    max_llm_requests: int
    summary: str
    agents: list[AgentPickOut]
    rules_version: str = ""
    needs: dict = Field(default_factory=dict)
    thresholds: dict = Field(default_factory=dict)
    watch: bool = False                   # a near miss: no rule fired, r > theta
    closest: Optional[str] = None         # the detector nearest its alert threshold, in plain words


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
    body: Optional[str] = None                     # message text; only on /falls/{id} (fixed template, de-identified)


class WeeklySendIn(BaseModel):
    force: bool = False


class WeeklySendOut(BaseModel):
    patient_id: str
    week: str
    queued: bool
    deduplicated: bool
    alert_id: Optional[str] = None
    send_real: bool
    mode: str
    outbox: list[OutboxOut]


class FallItem(BaseModel):
    id: int
    patient_id: str
    day: str
    ts: str
    sisfall_subject: Optional[str] = None
    probability: float
    probability_text: str
    peak_g: Optional[float] = None
    inactivity: bool
    day_status: Optional[str] = None
    alert_status: Optional[str] = None


class Waveform(BaseModel):
    fs_hz: int
    downsample_stride: int
    t_s: list[float]
    x_g: list[float]
    y_g: list[float]
    z_g: list[float]
    magnitude_g: list[float]
    impact_index: int            # index into the downsampled arrays
    impact_time_s: float


class FallDetail(FallItem):
    inactivity_observed_s: Optional[float] = None
    inactivity_required_s: Optional[float] = None
    threshold: Optional[float] = None
    injected: bool = False
    in_training_set_of_worker_model: Optional[bool] = None
    in_training_set_of_final_model: Optional[bool] = None
    waveform: Optional[Waveform] = None
    alert: Optional[AlertOut] = None
    outbox: list[OutboxOut] = []


class SimFallIn(BaseModel):
    patient_id: str
    subject: Optional[str] = Field(None, description="optional held-out SisFall subject (SA03, SA11, SA19, SE06)")
    activity: Optional[str] = Field(None, description="optional SisFall fall type, e.g. F02 (default: random)")


class SimFallOut(BaseModel):
    command_id: int
    status: Literal["DONE", "QUEUED"]
    patient_id: str
    day: Optional[str] = None
    event_id: Optional[int] = None
    sisfall_subject: str
    activity: str
    file: str
    in_training_set_of_worker_model: bool
    in_training_set_of_final_model: bool
    note: str


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


class ModelBudget(BaseModel):
    model: str
    rpm_limit: Optional[float] = None
    effective_rpm_cap: Optional[int] = None
    requests_last_minute: int
    requests_last_24h: int
    cooldown_remaining_s: float
    failures_last_24h: int


class LLMBudgetOut(BaseModel):
    models: list[ModelBudget]
    cache_hits: int
    model_requests: int
    cache_hit_rate: Optional[float] = None
    recent_events: list[dict[str, Any]]


class MetricsOut(BaseModel):
    available: list[str]
    files: dict[str, Any]


class QualityOut(BaseModel):
    live: dict[str, Any]
    static: Optional[dict[str, Any]] = None


class SystemConfigOut(BaseModel):
    demo: bool = False
    allow_kill: bool
    dry_run_mail: bool
    llm_enabled: bool


class SourceOut(BaseModel):
    id: str
    origin: str                     # local | pubmed
    title: str
    source: str                     # publisher (local guideline) or "PubMed"
    year: Optional[str] = None
    page: Optional[str] = None      # local guidelines
    journal: Optional[str] = None   # PubMed
    url: Optional[str] = None       # PubMed link when one exists
    excerpt: Optional[str] = None   # at most 40 words, local guidelines only (never PubMed abstract text)


# ---- live / emulator mode
class IngestResultOut(BaseModel):
    accepted: int
    duplicates: int
    rejected: int = 0
    patients: list[str]


class StreamOut(BaseModel):
    last_wall_age_s: Optional[float] = None        # seconds since the last event of this stream arrived (wall clock)
    state: str                                     # fresh | stale | idle (idle = a sparse stream with nothing expected right now) | none
    

class LivePatientOut(BaseModel):
    patient_id: str
    last_event_ts: Optional[str] = None            # the patient's clock: newest DATA time seen
    open_day: Optional[str] = None
    status: Optional[str] = None                   # triage status of the open day so far
    provisional: bool = True
    as_of: Optional[str] = None
    streams: dict[str, StreamOut] = Field(default_factory=dict)


class EmulatorOut(BaseModel):
    status: str                                    # idle | playing | paused | finished
    speed: float                                   # simulated seconds per wall second
    sim_now: Optional[str] = None
    cursor: int = 0
    total: int = 0
    start: Optional[str] = None
    end: Optional[str] = None
    patients: list[str] = Field(default_factory=list)


class LiveStatusOut(BaseModel):
    mode: str                                      # replay | emulator | live
    label: str                                     # what the viewer must know about where the data comes from
    emulator: Optional[EmulatorOut] = None
    patients: list[LivePatientOut] = Field(default_factory=list)
    now: float


class EmulatorCmdIn(BaseModel):
    action: str = Field(description="load | play | pause | speed | fall")
    speed: Optional[float] = None
    patient_id: Optional[str] = None
    subject: Optional[str] = None
    activity: Optional[str] = None
    start_day: Optional[str] = None
    n_days: Optional[int] = None
    n_patients: Optional[int] = None


class EmulatorCmdOut(BaseModel):
    status: str
    result: Optional[dict] = None
    note: str = ""


class LoginIn(BaseModel):
    token: str = Field(min_length=1, max_length=200)


class AuthOut(BaseModel):
    auth_enabled: bool
    role: Optional[str] = None                     # caregiver | clinician | operator | null (not logged in)


class ActivityEventOut(BaseModel):
    ts: float                                       # wall-clock seconds
    agent: str
    action: str
    text: str
    patient_id: str = ""
    day: str = ""
    ref: str = ""


class AgentSummaryOut(BaseModel):
    id: str
    name: str
    kind: str
    status: str
    detail: Optional[str] = None
    heartbeat_age_s: Optional[float] = None
    actions: int
    last_ts: Optional[float] = None
    last_text: Optional[str] = None
