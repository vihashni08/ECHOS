"""ECHOS v2 endpoints: the Review Coordinator's daily list, each Patient Monitor's scores and belief, clinician outcomes (the agents'
learning signal) and the demo 'critical reading' injection. Like the rest of the API this reads SQLite only and never loads a model."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from echos.api import services as V
from echos.api.deps import ERR, ctx_of
from echos.config import ROOT
from echos.orchestrator import state as S
from echos.team import bus

router = APIRouter(prefix="/api", responses=ERR)


class ReviewItem(BaseModel):
    day: str
    patient_id: str
    rank: int
    reason: str = Field(description="'priority' (highest lift) or 'fairness' (not reviewed for >= review.fairness_days)")
    priority: float = Field(description="max(vitals risk / its base rate, agitation risk / its base rate)")
    vitals_risk: Optional[float] = None
    agitation_risk: Optional[float] = None
    lift_vitals: Optional[float] = None
    lift_agitation: Optional[float] = None
    why: list[str] = []
    outcome: Optional[str] = Field(None, description="CONFIRMED / NO_EVENT once the clinician (or the TIHM record in replay) has answered")


class ReviewDay(BaseModel):
    day: Optional[str]
    k: int
    items: list[ReviewItem]
    deferred: list[str] = []
    days: list[str] = []


class MonitorDay(BaseModel):
    day: str
    status: Optional[str] = None
    vitals: dict = {}
    urgent: list[str] = []
    attention: list[str] = []
    agitation: list[dict] = []
    agitation_max: Optional[float] = None
    vitals_risk: Optional[float] = None
    rate: Optional[float] = None
    rate_ci95: Optional[list[float]] = None
    reasons: list[str] = []
    reviewed: Optional[int] = Field(None, description="rank on the review list that day, if selected")


class OutcomeIn(BaseModel):
    patient_id: str
    day: str
    vitals_event: bool = Field(False, description="the clinician confirmed a BP / pulse problem")
    agitation_windows: list[str] = Field(default_factory=list, description="window end times (ISO) with confirmed agitation")


class ReadingIn(BaseModel):
    patient_id: str
    sys: Optional[float] = Field(None, ge=40, le=300)
    dia: Optional[float] = Field(None, ge=20, le=200)
    hr: Optional[float] = Field(None, ge=20, le=250)
    temp: Optional[float] = Field(None, ge=30, le=45)


def _review_rows(c, day: str) -> list[dict]:
    rows = c.execute(select(S.review_list).where(S.review_list.c.day == day).order_by(S.review_list.c.rank)).fetchall()
    return [{"day": r.day, "patient_id": r.patient_id, "rank": r.rank, "reason": r.reason, "priority": round(r.priority or 0, 3),
             "vitals_risk": r.vitals_risk, "agitation_risk": r.agitation_risk, "lift_vitals": r.lift_vitals, "lift_agitation": r.lift_agitation,
             "why": json.loads(r.why or "[]"), "outcome": r.outcome} for r in rows]


@router.get("/v2/review", response_model=ReviewDay, tags=["v2"])
def review_day(request: Request, day: Optional[str] = None):
    """The Review Coordinator's list for a day (latest allocated day if none is given), plus the patients it deferred."""
    ctx = ctx_of(request)
    with ctx.eng.begin() as c:
        days = [r[0] for r in c.execute(select(S.review_list.c.day).distinct().order_by(S.review_list.c.day.desc()))]
        d = day or (days[0] if days else None)
        items = _review_rows(c, d) if d else []
        dec = c.execute(select(S.agent_message.c.payload).where((S.agent_message.c.topic == "review_decision") & (S.agent_message.c.day == d))
                        .order_by(S.agent_message.c.id.desc())).first() if d else None
    pl = json.loads(dec[0]) if dec else {}
    return {"day": d, "k": int(pl.get("k") or ctx.cfg.get("review", {}).get("k_per_day", 4)), "items": items, "deferred": pl.get("deferred", []), "days": days}


@router.get("/v2/patients/{pid}/monitor", response_model=list[MonitorDay], tags=["v2"])
def monitor_timeline(request: Request, pid: str, limit: int = Query(28, ge=1, le=120)):
    """What this person's Patient Monitor saw and concluded, newest day first: readings vs personal usual, urgent / attention flags,
    agitation windows with reasons, the personal event rate with its 95% credible interval, and whether the Coordinator picked them."""
    ctx = ctx_of(request)
    V.require_patient(ctx, pid)
    with ctx.eng.begin() as c:
        outs = c.execute(select(S.agent_output.c.day, S.agent_output.c.result).where((S.agent_output.c.agent_id == "patient_monitor") & (S.agent_output.c.patient_id == pid))
                         .order_by(S.agent_output.c.event_id.desc())).fetchall()
        reqs = {r.day: json.loads(r.payload) for r in c.execute(select(S.agent_message.c.day, S.agent_message.c.payload).where(
            (S.agent_message.c.topic == "review_request") & (S.agent_message.c.patient_id == pid)).order_by(S.agent_message.c.id))}
        status = dict(c.execute(select(S.case_state.c.day, S.case_state.c.status).where(S.case_state.c.patient_id == pid)).fetchall())
        ranks = dict(c.execute(select(S.review_list.c.day, S.review_list.c.rank).where(S.review_list.c.patient_id == pid)).fetchall())
    by_day: dict = {}
    for day, res in outs:
        r = json.loads(res)
        d = by_day.setdefault(day, {"day": day, "vitals": {}, "urgent": [], "attention": [], "agitation": []})
        if r.get("reading"):
            d["urgent"] += r.get("safety") or []
            continue
        d.update({"vitals": r.get("vitals") or {}, "agitation": [{k: v for k, v in w.items() if k != "idx"} for w in r.get("agitation") or []],
                  "agitation_max": r.get("agitation_max")})
        d["urgent"] += r.get("safety") or []
        d["attention"] += r.get("attention") or []
    out = []
    for day in sorted(by_day, reverse=True)[:limit]:
        d, q = by_day[day], reqs.get(day) or {}
        out.append({**d, "status": status.get(day), "vitals_risk": q.get("vitals_risk"), "rate": q.get("rate"), "rate_ci95": q.get("rate_ci95"),
                    "reasons": q.get("reasons") or [], "reviewed": ranks.get(day)})
    return out


@router.post("/v2/reviews/outcome", tags=["v2"])
def review_outcome(request: Request, body: OutcomeIn):
    """A clinician's answer after a review (confirmed problem or not). It goes to the Care agent, which passes it to the patient's Patient
    Monitor as FEEDBACK: the monitor updates its personal event rate and episode history from it."""
    ctx = ctx_of(request)
    V.require_patient(ctx, body.patient_id)
    mid = bus.post(ctx.eng, "clinician", "care", "clinician_outcome", body.patient_id, body.day,
                   {"vitals_event": body.vitals_event, "agitation_windows": body.agitation_windows})
    return {"message_id": mid, "status": "QUEUED"}


@router.post("/sim/reading", tags=["simulation"])
def sim_reading(request: Request, body: ReadingIn):
    """Demo: inject one vitals reading for a replay patient at the current simulated time. The Patient Monitor checks it at once; a red-flag
    value (NEWS2 red score, BP >= 180/120) becomes an URGENT alert through the Care agent."""
    ctx = ctx_of(request)
    V.require_patient(ctx, body.patient_id)
    if all(getattr(body, k) is None for k in ("sys", "dia", "hr", "temp")):
        raise HTTPException(422, "give at least one of sys, dia, hr, temp")
    cid, st, res = V.send_command(ctx, "inject_reading", body.model_dump(), wait_s=5.0)
    if st == "ERROR":
        raise HTTPException(409, res or "injection failed")
    return {"command_id": cid, "status": "DONE" if st == "DONE" else "QUEUED", **(json.loads(res) if st == "DONE" and res else {})}


@router.get("/v2/summary", tags=["v2"])
def v2_summary(request: Request):
    """Counts for the dashboard header: days scored, urgent alerts, reviews (and how many found a confirmed event), messages per topic."""
    ctx = ctx_of(request)
    with ctx.eng.begin() as c:
        scored = c.execute(select(func.count()).select_from(S.agent_output).where(S.agent_output.c.agent_id == "patient_monitor")).scalar()
        urgent = c.execute(select(func.count()).select_from(S.alerts)).scalar()
        reviews = dict(c.execute(select(S.review_list.c.outcome, func.count()).group_by(S.review_list.c.outcome)).fetchall())
        topics = dict(c.execute(select(S.agent_message.c.topic, func.count()).group_by(S.agent_message.c.topic)).fetchall())
        status = dict(c.execute(select(S.case_state.c.status, func.count()).group_by(S.case_state.c.status)).fetchall())
        cards = dict(c.execute(select(S.evidence_card.c.source, func.count()).group_by(S.evidence_card.c.source)).fetchall())
    return {"evidence_cards": cards, "patient_days_scored": scored, "urgent_alerts": urgent, "reviews": sum(reviews.values()),
            "reviews_confirmed": reviews.get("CONFIRMED", 0), "reviews_no_event": reviews.get("NO_EVENT", 0), "messages": topics, "case_status": status}


# ---- Evidence agent cards and the decision trace (the 'Decision Trace' screen)
class EvidenceCard(BaseModel):
    card_id: str
    patient_id: str
    day: str
    kind: str = Field(description="'urgent' (an alert) or 'review' (an assigned review)")
    source: str = Field(description="'gemini' (validated generated text) or 'template' (fixed wording)")
    explanation: str
    citations: list[str] = []
    passages: list[dict] = Field(default=[], description="retrieved guideline passages: id, source, url, page, text, score")
    validator: dict = {}
    model: Optional[str] = None


def _cards(c, day: Optional[str], pid: Optional[str]) -> list[dict]:
    q = select(S.evidence_card).order_by(S.evidence_card.c.created_ts)
    if day:
        q = q.where(S.evidence_card.c.day == day)
    if pid:
        q = q.where(S.evidence_card.c.patient_id == pid)
    out = []
    for r in c.execute(q).fetchall():
        d = json.loads(r.card)
        out.append({"card_id": r.card_id, "patient_id": r.patient_id, "day": r.day, "kind": r.kind, "source": r.source, "explanation": d.get("explanation", ""),
                    "citations": d.get("citations") or [], "passages": d.get("passages") or [], "validator": d.get("validator") or {}, "model": d.get("model")})
    return out


@router.get("/v2/evidence", response_model=list[EvidenceCard], tags=["v2"])
def evidence(request: Request, day: Optional[str] = None, patient_id: Optional[str] = None):
    """Evidence cards written by the Evidence agent: a short explanation citing guideline passages for each urgent alert and assigned review."""
    ctx = ctx_of(request)
    with ctx.eng.begin() as c:
        return _cards(c, day, patient_id)


@router.get("/v2/trace/{pid}/{day}", tags=["v2"])
def decision_trace(request: Request, pid: str, day: str):
    """Everything the agents did for one person on one day, in order: the Patient Monitor's scores, its review request, the Coordinator's decision,
    the Care agent's task / alert, the Evidence card, and the bus messages that connected them."""
    ctx = ctx_of(request)
    V.require_patient(ctx, pid)
    with ctx.eng.begin() as c:
        res = c.execute(select(S.agent_output.c.result).where((S.agent_output.c.agent_id == "patient_monitor") & (S.agent_output.c.patient_id == pid)
                                                              & (S.agent_output.c.day == day)).order_by(S.agent_output.c.event_id)).fetchall()
        msgs = c.execute(select(S.agent_message).where((S.agent_message.c.patient_id == pid) & (S.agent_message.c.day == day))
                         .order_by(S.agent_message.c.id)).fetchall()
        review = c.execute(select(S.review_list).where((S.review_list.c.patient_id == pid) & (S.review_list.c.day == day))).first()
        case = c.execute(select(S.case_state.c.status).where((S.case_state.c.patient_id == pid) & (S.case_state.c.day == day))).scalar()
        task = c.execute(select(S.care_task).where((S.care_task.c.patient_id == pid) & (S.care_task.c.day == day))).first()
        alerts = c.execute(select(S.alerts.c.alert_id, S.alerts.c.event_time).where((S.alerts.c.patient_id == pid) & S.alerts.c.alert_id.like(f"{pid}|{day}|%"))).fetchall()
        cards = _cards(c, day, pid)
        decision = c.execute(select(S.agent_message.c.payload).where((S.agent_message.c.topic == "review_decision") & (S.agent_message.c.day == day))
                             .order_by(S.agent_message.c.id.desc())).first()
    monitor = json.loads(res[0][0]) if res else None
    req = next((json.loads(m.payload) for m in msgs if m.topic == "review_request"), None)
    return {"patient_id": pid, "day": day, "status": case, "monitor": monitor, "review_request": req,
            "review": ({k: getattr(review, k) for k in ("rank", "reason", "priority", "vitals_risk", "agitation_risk", "lift_vitals", "lift_agitation", "outcome")} if review else None),
            "coordinator": json.loads(decision[0]) if decision else None,
            "care_task": ({"title": task.title, "detail": task.detail, "status": task.status} if task else None),
            "alerts": [{"alert_id": a.alert_id, "event_time": a.event_time} for a in alerts], "evidence": cards,
            "messages": [{"id": m.id, "sender": m.sender, "recipient": m.recipient, "topic": m.topic, "status": m.status} for m in msgs]}


@router.get("/v2/patients/{pid}/memory", tags=["v2"])
def patient_memory(request: Request, pid: str):
    """What one Patient Monitor remembers about its person: a summary of the saved state (a few numbers, not a trained model)."""
    ctx = ctx_of(request)
    V.require_patient(ctx, pid)
    with ctx.eng.begin() as c:
        row = c.execute(select(S.patient_agent.c.state, S.patient_agent.c.updated_ts).where(S.patient_agent.c.patient_id == pid)).first()
    if not row:
        return {"patient_id": pid, "known": False}
    st = json.loads(row[0])
    vh = st.get("vit_hist") or {}
    sh = st.get("slot_hist") or {}
    k, n = int(st.get("k") or 0), int(st.get("n") or 0)
    numbers = sum(len(v) for v in vh.values()) + 2 + sum(len(w) * 16 for w in sh.values()) + len(st.get("episode_windows") or [])
    return {"patient_id": pid, "known": True, "updated_ts": row[1], "vitals_history": {v: (vals[-28:]) for v, vals in vh.items()},
            "events_k": k, "days_n": n, "windows_seen": int(st.get("windows_seen") or 0), "episodes": len(st.get("episode_windows") or []),
            "slot_history_windows": {slot: len(w) for slot, w in sh.items()}, "days_scored": len(st.get("days_done") or []),
            "pending_days": sorted((st.get("pending") or {}).keys()), "numbers_stored": numbers}


# ---- data behind the interactive demo screens (scripts/evaluation/demo_data.py, scripts/evaluation/team_e2e.py)
def _json_file(path: Path):
    if not path.exists():
        raise HTTPException(404, f"{path.name} missing: run PYTHONPATH=. python scripts/evaluation/demo_data.py")
    return json.loads(path.read_text())


@router.get("/v2/demo/audit", tags=["v2"])
def demo_audit():
    """Aggregate grid of patient-days by (systolic, diastolic) bin and how many carry a BP label: the 160/100 wall of the label audit."""
    return _json_file(ROOT / "reports" / "metrics" / "v2_demo_audit.json")


@router.get("/v2/demo/budget", tags=["v2"])
def demo_budget():
    """Events per review for every policy at K = 1..10 and several fairness floors (out-of-fold replay)."""
    return _json_file(ROOT / "reports" / "metrics" / "v2_demo_budget.json")


@router.get("/v2/demo/game", tags=["v2"])
def demo_game():
    """'You vs ECHOS': days with what a clinician could see for each (de-identified) person, what ECHOS ranked, and what TIHM later confirmed."""
    g = _json_file(ROOT / "data" / "v2" / "game.json")
    for d in g["days"]:
        for p in d["patients"]:
            p.pop("real_id", None)
    return g


@router.get("/v2/runtime", tags=["v2"])
def runtime_report():
    """The crash test: the reference run and the run with the Patient Monitor killed, side by side (reports/metrics/v2_runtime.json)."""
    return _json_file(ROOT / "reports" / "metrics" / "v2_runtime.json")
