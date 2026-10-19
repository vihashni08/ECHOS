"""Replay orchestrator = the Triage agent as a plain sequential pipeline:
load_day -> run_agents -> triage -> [immediate_alert] -> investigate (Case Investigator) -> daily_report -> [weekly_report].

The first three steps are cheap and deterministic: they are recomputed on every run. The side-effecting steps are idempotent (alerts have a unique key) and their
progress per (patient, day) is kept in the `agent_checkpoint` table (agent_id `case:<patient>:<day>`), so after a crash a resume continues with the first step that
did not finish and can never produce a duplicate alert. (This replaced a LangGraph graph with a SQLite checkpoint file.)
"""
from __future__ import annotations

import json
import time
from typing import Callable, Optional, TypedDict

import pandas as pd
from sqlalchemy import select

from echos.config import load_config
from echos.orchestrator import state as S
from echos.alerts.dispatcher import AlertDispatcher
from echos.alerts.mailer import make_mailer
from echos.orchestrator.alerts import queue_alerts
from echos.orchestrator.triage import triage as run_triage
from echos.team.planner import TeamPlan, plan_team, record_plan


class CaseState(TypedDict, total=False):
    patient_id: str
    day: str
    events: dict
    results: dict
    days_since_vitals: int
    decision: dict
    plan: dict
    weekly: bool


SIDE_STEPS = ("immediate_alert", "investigate", "daily_report", "weekly_report")
FINISHED = -1


class Pipeline:
    """One patient-day at a time. `dispatcher`: the AlertDispatcher (tests / shutdown: pipeline.dispatcher.flush())."""

    def __init__(self, eng, steps: dict, dispatcher):
        self.eng, self.steps, self.dispatcher = eng, steps, dispatcher

    def _progress(self, key: str) -> int:
        with self.eng.begin() as c:
            v = c.execute(select(S.agent_checkpoint.c.last_event_id).where(S.agent_checkpoint.c.agent_id == key)).scalar()
        return 0 if v is None else int(v)

    def _save(self, key: str, n: int) -> None:
        with self.eng.begin() as c:
            S.upsert(c, S.agent_checkpoint, ["agent_id"], agent_id=key, last_event_id=n, ts=S.now())

    def run(self, patient_id: str, day: str) -> bool:
        """Run (or resume) one patient-day. Returns False if it was already finished."""
        key = f"case:{patient_id}:{day}"
        done = self._progress(key)
        if done == FINISHED:
            return False
        st: CaseState = {"patient_id": patient_id, "day": day}
        for name in ("load_day", "run_agents", "triage"):
            st.update(self.steps[name](st))
        todo = [n for n in SIDE_STEPS if (n != "immediate_alert" or st["decision"]["status"] == "URGENT") and (n != "weekly_report" or st.get("weekly"))]
        for i, name in enumerate(todo):
            if i >= done:
                st.update(self.steps[name](st))
                self._save(key, i + 1)
        self._save(key, FINISHED)
        return True


def build_pipeline(eng, fault: Optional[Callable[[str], None]] = None, inline_agents=None,
                   report_llm="auto", report_root=None, gate=None, dispatcher=None, weekly_pdf=None) -> Pipeline:
    """fault(node_name) may raise to simulate a crash. inline_agents: {agent_id: fn(event_row)->dict} used only
    when an agent's stored output is missing (tests / no-process mode). report_llm: "auto" = Gemini if configured
    (llm.model + key), None/False = template mode. gate: NORMAL days get a template daily report (no LLM, no retrieval);
    None = config `report_gate.enabled`. The gate only touches the report node, never triage or alerts.
    dispatcher: AlertDispatcher (default: config alerts.send_real false -> DryRunMailer; true -> SMTP). Sending runs in its own thread."""
    from echos.reports.daily.agent import ReportAgent
    from echos.reports.daily.facts import build_day_facts
    from echos.reports.llm.llm import make_llm
    from echos.reports.llm.llm_budget import make_budget
    auto = report_llm == "auto"   # real Gemini if configured: daily report through the budget manager (cache -> primary -> fallback -> template)
    reporter = ReportAgent(eng, make_llm() if auto else (report_llm or None), root=report_root, budget=make_budget() if auto else None)
    fault = fault or (lambda node: None)
    if dispatcher is None:
        dispatcher = AlertDispatcher(eng, make_mailer())
        dispatcher.recover()              # retries left RETRYING / PENDING by a previous process (state is in the outbox table)
        dispatcher.start_sweeper()        # FAILED alert rows are retried every alerts.sweep_interval_s for alerts.sweep_window_s
    wc = load_config().get("weekly", {})
    weekly_on = bool(wc.get("enabled", True)) if weekly_pdf is None else bool(weekly_pdf)
    weekly_llm = bool(wc.get("llm_narrative_in_simulation", False))
    gate_on = bool(load_config().get("report_gate", {}).get("enabled", True)) if gate is None else bool(gate)
    def load_day(st: CaseState) -> dict:
        with eng.begin() as c:
            evs = c.execute(select(S.event_log).where((S.event_log.c.patient_id == st["patient_id"]) &
                                                      (S.event_log.c.day == st["day"]))).fetchall()
            prev = c.execute(select(S.case_state.c.days_since_vitals, S.case_state.c.day).where(
                (S.case_state.c.patient_id == st["patient_id"]) & (S.case_state.c.day < st["day"]))
                .order_by(S.case_state.c.day.desc()).limit(1)).first()
        d = pd.Timestamp(st["day"])
        events: dict = {}
        for e in evs:
            events.setdefault(e.kind, []).append(e.event_id)
        return {"events": events, "days_since_vitals": prev[0] if prev else -1, "weekly": d.weekday() == 6}

    def run_agents(st: CaseState) -> dict:
        fault("run_agents")
        res = {}
        with eng.begin() as c:
            for agent, kinds in S.AGENT_KINDS.items():
                key = "health" if agent == "health_monitoring" else agent
                eids = [i for k in kinds for i in st["events"].get(k, [])]
                rows = c.execute(select(S.agent_output).where((S.agent_output.c.agent_id == agent) &
                                                              S.agent_output.c.event_id.in_(eids or [-1]))).fetchall()
                outs = [json.loads(r.result) for r in rows]
                if agent == "health_monitoring" and eids and not outs and not (inline_agents and agent in inline_agents) \
                        and agent_down(eng, agent):
                    from echos.agents.health_monitoring import fallback_predict  # worker down: fixed vitals thresholds
                    outs = [fallback_predict(st["patient_id"], st["day"], "worker_down").model_dump(mode="json")]
                    eids = []  # complete: the fallback stands in for the missing event output
                if len(outs) < len(eids) and inline_agents and agent in inline_agents:
                    for e in c.execute(select(S.event_log).where(S.event_log.c.event_id.in_(eids))).fetchall():
                        if e.event_id not in {r.event_id for r in rows}:
                            outs.append(inline_agents[agent](e))
                if eids and len(outs) < len(eids):
                    res[key] = None  # incomplete -> triage reports a data gap, never NORMAL
                elif outs:
                    res[key] = outs[-1] if agent == "health_monitoring" else max(outs, key=lambda o: o["score"])  # worst case
                else:
                    res[key] = None if agent != "fall" else {"score": 0.0, "evidence": {}, "flag": False}
        has_vitals = bool((res.get("health") or {}).get("vitals", {}).get("available"))
        prev = st["days_since_vitals"]
        dsv = 0 if has_vitals else (1 if prev < 0 else prev + 1)
        q = res.get("quality")
        if q and q["evidence"].get("quality_checked", True) and q["evidence"].get("days_since_last_reading") is not None:
            dsv = int(q["evidence"]["days_since_last_reading"])  # Agent 0 is the source of truth
        return {"results": res, "days_since_vitals": dsv}

    def triage_node(st: CaseState) -> dict:
        dec = run_triage(st["results"], st["days_since_vitals"])
        with eng.begin() as c:
            S.upsert(c, S.case_state, ["patient_id", "day"], patient_id=st["patient_id"], day=st["day"],
                     status=dec.status.value, data_gap=dec.data_gap, days_since_vitals=st["days_since_vitals"],
                     reasons=S.j(dec.reasons), report=None, updated_ts=S.now())
        decision = dec.model_dump(mode="json")
        plan = plan_team(decision, st["results"])                       # Team Planner: which agents this case needs, and why (recorded on the bus)
        record_plan(eng, st["patient_id"], st["day"], plan)
        return {"decision": decision, "plan": plan.model_dump(mode="json")}

    def investigate(st: CaseState) -> dict:
        """Case Investigator agent: plans a few checks and writes the case file (never changes the status; a failure never stops the case)."""
        from echos.team.investigator import run_investigation
        if st.get("plan") and not TeamPlan(**st["plan"]).has("investigator"):
            return {}                                                    # the plan did not call for it (Normal day)
        run_investigation(eng, st["patient_id"], st["day"], st["decision"], st["results"])
        return {}

    def immediate_alert(st: CaseState) -> dict:
        queue_alerts(eng, dispatcher, st["patient_id"], st["day"], st["decision"], st["results"])   # fixed template, no LLM: before any report work
        fault("immediate_alert")  # crash point AFTER the side effect: resume must not duplicate it
        return {}

    def daily_report(st: CaseState) -> dict:  # Report agent: also runs on URGENT days (alerts + times belong in the report)
        facts = build_day_facts(eng, st["patient_id"], st["day"], st["decision"], st["results"])
        use_llm = not (gate_on and st["decision"]["status"] == "NORMAL")  # gate: NORMAL -> template, no LLM, no RAG
        out = reporter.daily(facts, defer_llm=True, use_llm=use_llm)  # template now, LLM wording later: never blocks alerts / next cases
        with eng.begin() as c:
            c.execute(S.case_state.update().where((S.case_state.c.patient_id == st["patient_id"]) &
                                                  (S.case_state.c.day == st["day"])).values(report=str(out["path"].name)))
        return {}

    def weekly_report(st: CaseState) -> dict:
        """Week end: weekly PDF (data -> narrative -> PDF -> email to the clinician) in the report background worker, so it never blocks the next
        case, triage or alerts. Any failure is logged and the day goes on."""
        if not weekly_on:
            return {}
        from echos.reports.weekly.weekly_report import make_weekly
        pid, day = st["patient_id"], st["day"]
        use_llm = bool(weekly_llm and reporter.budget is not None)
        reporter._defer(lambda: make_weekly(eng, pid, day, dispatcher, budget=reporter.budget, retriever=reporter.retriever, use_llm=use_llm,
                                            root=reporter.root), ctx=f"weekly_pdf {pid} {day}")
        return {}

    steps = {"load_day": load_day, "run_agents": run_agents, "triage": triage_node, "immediate_alert": immediate_alert, "investigate": investigate,
             "daily_report": daily_report, "weekly_report": weekly_report}
    return Pipeline(eng, steps, dispatcher)


def agent_down(eng, agent: str) -> bool:
    """An agent is treated as down when its heartbeat is missing or stale (the rule the workers use for Agent 0).
    quality down -> days are triaged with 'quality unchecked'; health_monitoring down -> fixed vitals thresholds,
    'monitoring degraded'. Either way the day is never NORMAL by omission."""
    with eng.begin() as c:
        hb = c.execute(select(S.agent_heartbeat.c.ts).where(S.agent_heartbeat.c.agent_id == agent)).first()
    return hb is None or time.time() - hb[0] > 3 * load_config()["runtime"]["heartbeat_s"] + 10


def day_ready(eng, patient_id: str, day: str, optional=frozenset()) -> bool:
    """All events of the day exist (the 'day' event is emitted last) and every agent has output for each.
    optional: agents that are down, whose output is not required (a bool is accepted for the old quality flag)."""
    optional = {"quality"} if optional is True else set() if optional is False else set(optional)
    with eng.begin() as c:
        evs = c.execute(select(S.event_log.c.event_id, S.event_log.c.kind).where(
            (S.event_log.c.patient_id == patient_id) & (S.event_log.c.day == day))).fetchall()
        if not any(k == "day" for _, k in evs):
            return False
        done = {(r.agent_id, r.event_id) for r in c.execute(select(S.agent_output.c.agent_id, S.agent_output.c.event_id).where(
            (S.agent_output.c.patient_id == patient_id) & (S.agent_output.c.day == day)))}
    return all((a, eid) in done for a, kinds in S.AGENT_KINDS.items() for eid, k in evs
               if k in kinds and a not in optional)


def process_case(pipeline: Pipeline, patient_id: str, day: str) -> bool:
    """Run (or resume) one patient-day. Returns False if it was already finished."""
    return pipeline.run(patient_id, day)


def run_orchestrator(eng, pipeline, stop=None, poll_s=0.2, until_done=None, timeout=None) -> int:
    """Process every ready patient-day in chronological order until no unprocessed days remain."""
    t0, n, finished = time.time(), 0, set()
    while not (stop and stop.is_set()):
        producer_done = bool(until_done and until_done())  # read BEFORE the snapshot: a slow pass must not miss late days
        with eng.begin() as c:
            days = c.execute(select(S.event_log.c.patient_id, S.event_log.c.day).where(S.event_log.c.kind == "day")
                             .order_by(S.event_log.c.day, S.event_log.c.patient_id)).fetchall()
        progressed, remaining = False, 0
        q_opt = {a for a in ("quality", "health_monitoring") if agent_down(eng, a)}
        for p, d in days:
            if (p, d) in finished:
                continue
            if day_ready(eng, p, d, q_opt):
                n += process_case(pipeline, p, d)
                finished.add((p, d))
                progressed = True
            else:
                remaining += 1
        if producer_done and remaining == 0:
            break
        if timeout and time.time() - t0 > timeout:
            break
        if not progressed:
            time.sleep(poll_s)
    return n
