"""Triage stage 2: decides, for every patient-day, WHICH agents the case needs, and writes down why.

Stage 1 (echos/orchestrator/triage.py) classifies the status with if-then rules. Stage 2 (this file) routes the case with one score and one cutoff:

  NEAREST-THRESHOLD RATIO   r = max_k ( p_k / tau_k )      p_k = score of detector k (vitals, fall), tau_k = that detector's own alert
                            threshold (config `triage`). r = 1 means "exactly at its alert threshold"; a fixed vitals rule that fired while monitoring is degraded counts r = 1.
  ROUTING RULE              rule fired (status MONITOR / URGENT)  ->  Investigator + Care Coordinator (hard evidence, no cutoff involved)
                            no rule fired and  r > theta          ->  Investigator writes a short WATCH file (a near miss); nobody else is called
                            otherwise                              ->  Triage + template report only
                            URGENT always makes the Coordinator the owner of the alert: no cutoff can change that.
  CUTOFF theta              chosen by a CALL BUDGET, label-free: the smallest value for which false calls (watch files on Normal days) stay <= B per patient-week,
                            i.e. the (1 - B * patient_weeks / n_normal) quantile of r on the replayed Normal days (config `planner.theta`,
                            scripts/tuning/choose_routing_threshold.py, echos/team/routing_budget.py). theta is in plain units: a fraction of the detector's own alert threshold.

r is a routing score, NOT a clinical probability. The plan never changes the triage status, never delays or suppresses an alert, and the detectors are not
part of it (sensing is always on). No model is trained: there are no labels for "the right agents". The plan is recorded on the bus (sender `planner`, topic
`team_plan`). Equations and replay evidence: docs/TEAM_DESIGN.md."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from echos.config import load_config
from echos.team import bus

AGENT = "planner"
RULES_VERSION = "3"
MAX_LLM_REQUESTS = 5      # echos/reports/daily/agent.py: 3 tool steps + 1 final + 1 retry
DEFAULT_THETA = 1.0       # no `planner.theta` in the config: r > 1 would mean a detector is past its own threshold, so nothing is a near miss


class AgentPick(BaseModel):
    id: str
    active: bool
    role: str = ""            # what the agent does on THIS case ("owner" / "passive" / "watch" / "template" ...)
    reason: str               # one plain sentence: why it is (not) needed
    need: Optional[float] = None        # the ratio r (None = always on)
    threshold: Optional[float] = None   # the cutoff theta it had to exceed (None = decided by a fired rule, not by the cutoff)


class TeamPlan(BaseModel):
    status: str
    agents: list[AgentPick]
    team_size: int            # number of active agents (of the four)
    max_llm_requests: int     # upper bound for the daily report on this case (0 = template only)
    summary: str
    rules_version: str = RULES_VERSION
    skipped: list[str] = Field(default_factory=list)
    needs: dict = Field(default_factory=dict)          # {"r": nearest-threshold ratio}
    thresholds: dict = Field(default_factory=dict)     # {"theta": cutoff}
    watch: bool = False                                 # a near miss: no rule fired, r > theta
    closest: Optional[str] = None                       # the detector nearest its alert threshold (plain words)

    def has(self, agent_id: str) -> bool:
        return any(a.id == agent_id and a.active for a in self.agents)

    @property
    def active_ids(self) -> list[str]:
        return [a.id for a in self.agents if a.active]


def theta(cfg: Optional[dict] = None) -> float:
    """The cutoff: `cfg` override, else config planner.theta, else DEFAULT_THETA."""
    v = (cfg or {}).get("theta", (load_config().get("planner") or {}).get("theta"))
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else DEFAULT_THETA


def detector_scores(results: Optional[dict], t: dict) -> dict:
    """k -> {p, tau, r} for the detectors that produced a score, r = p / tau. A fixed vitals rule that fired while monitoring was degraded counts as r = 1."""
    h = (results or {}).get("health") or {}
    v, f = h.get("vitals") or {}, (results or {}).get("fall") or {}
    out = {}

    def add(k, p, tau):
        if p is not None and tau:
            out[k] = {"p": float(p), "tau": float(tau), "r": float(p) / float(tau)}
    if h.get("degraded"):
        if v.get("fixed_rule_triggered"):
            out["vitals"] = {"p": 1.0, "tau": 1.0, "r": 1.0}
    else:
        if v.get("available") and "probability" in v:
            add("vitals", v["probability"], t["vitals_prob"])
    if f and f.get("score") is not None:
        add("fall", f["score"], t["fall_prob"])
    return out


def nearest(sc: dict) -> tuple[float, Optional[str]]:
    """r = max_k r_k and the detector that gives it (0.0, None when no detector produced a score)."""
    if not sc:
        return 0.0, None
    k = max(sc, key=lambda x: sc[x]["r"])
    return sc[k]["r"], k


def plan_team(decision: dict, results: Optional[dict] = None, cfg: Optional[dict] = None, triage_cfg: Optional[dict] = None) -> TeamPlan:
    """decision: TriageDecision as dict (status, reasons, data_gap, urgent_types). results: detector outputs (scores for r, degraded note).
    cfg {"theta": x} overrides config `planner.theta`; triage_cfg overrides config `triage` (used by the evaluation scripts and the tests)."""
    status = str(decision.get("status") or "NORMAL")
    gap = bool(decision.get("data_gap"))
    utypes = list(decision.get("urgent_types") or [])
    degraded = bool(((results or {}).get("health") or {}).get("degraded"))
    urgent = status == "URGENT" or bool(utypes)
    hard = status != "NORMAL"                                            # a rule fired: the status itself is the evidence
    th = theta(cfg)
    sc = detector_scores(results, triage_cfg or load_config()["triage"])
    r, k = nearest(sc)
    watch = (not hard) and r > th
    inv = hard or watch
    closest = f"{k} is at {r * 100:.0f}% of its alert threshold" if k else None

    if hard:
        why = ("urgent " + (" and ".join(x.replace("_", " ") for x in utypes) or "day")) if urgent else \
              ("data gap: the case file explains what is missing" if gap else "a change from the person's own usual")
        inv_role, inv_reason = "owner", f"{why}: the evidence behind it has to be checked and written down"
    elif watch:
        inv_role, inv_reason = "watch", f"a near miss: {closest} (cutoff {th * 100:.0f}%), no rule fired; a short watch file is written, nobody else is called"
    else:
        inv_role, inv_reason = "", "nothing differs from the person's own usual, so there is nothing to investigate"
    if urgent:
        co_role, co_reason = "owner", "an urgent alert must be owned until someone acknowledges it (delivery, escalation, check-in)"
    elif hard:
        co_role, co_reason = "passive", "receives the case file so repeated changed days open one clinician review; no alert to own"
    else:
        co_role, co_reason = "", "no alert and no changed day to count"
    rep_role = "llm_eligible" if hard else "template"
    rep_reason = ("the caregiver needs the explanation in plain words; the LLM wording is optional and checked by a non-LLM validator" if hard
                  else "the daily report is still written, from a fixed template (no LLM, no retrieval)")
    picks = [AgentPick(id="triage", active=True, role="owner", reason="every case needs exactly one status, and nothing else starts without it"),
             AgentPick(id="investigator", active=inv, role=inv_role, reason=inv_reason, need=round(r, 3), threshold=None if hard else round(th, 3)),
             AgentPick(id="coordinator", active=hard, role=co_role, reason=co_reason),
             AgentPick(id="report", active=True, role=rep_role, reason=rep_reason)]
    active = [p for p in picks if p.active]
    skipped = [p.id for p in picks if not p.active]
    llm = MAX_LLM_REQUESTS if hard else 0
    names = ", ".join(p.id for p in active)
    note = " Monitoring is degraded (fixed vitals thresholds), so the case file will say so." if degraded and hard else ""
    summary = (f"{status}{' (watch)' if watch else ''}: {len(active)} of 4 agents ({names})" + (f"; not needed: {', '.join(skipped)}" if skipped else "") +
               f"; report uses {'a template' if not hard else f'at most {llm} LLM requests'}." + note)
    return TeamPlan(status=status, agents=picks, team_size=len(active), max_llm_requests=llm, summary=summary, skipped=skipped,
                    needs={"r": round(r, 4)}, thresholds={"theta": round(th, 4)}, watch=watch, closest=closest)


def record_plan(eng, patient_id: str, day: str, plan: TeamPlan) -> Optional[int]:
    """Write the plan on the bus (a broadcast: nobody consumes it, the Agent activity page and the API read it). A bus problem never stops the case."""
    import logging
    try:
        return bus.post(eng, AGENT, "all", "team_plan", patient_id, day, plan.model_dump(mode="json"))
    except Exception:  # noqa: BLE001
        logging.getLogger("echos.planner").exception("could not record the team plan for %s %s", patient_id, day)
        return None


def latest_plan(eng, patient_id: str, day: Optional[str] = None) -> Optional[dict]:
    """The newest recorded plan for a patient (and day, default: latest day with a plan), or None."""
    import json

    from sqlalchemy import select

    from echos.orchestrator import state as S
    q = select(S.agent_message).where((S.agent_message.c.sender == AGENT) & (S.agent_message.c.topic == "team_plan") & (S.agent_message.c.patient_id == patient_id))
    if day:
        q = q.where(S.agent_message.c.day == day)
    with eng.begin() as c:
        r = c.execute(q.order_by(S.agent_message.c.day.desc(), S.agent_message.c.id.desc()).limit(1)).first()
    return None if r is None else {"patient_id": r.patient_id, "day": r.day, "ts": r.ts, **json.loads(r.payload or "{}")}
