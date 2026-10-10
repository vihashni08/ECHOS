"""LangGraph orchestrator: load_day -> run_agents -> triage -> (immediate_alert | daily_report) -> [weekly_report].

Checkpointed per (patient, day) thread with langgraph-checkpoint-sqlite's SqliteSaver. Side-effecting nodes are
idempotent (alerts have a unique key), so a crash and resume can never produce a duplicate alert.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Callable, Optional, TypedDict

import pandas as pd
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select

from echos.config import ROOT, load_config
from echos.orchestrator import state as S
from echos.orchestrator.triage import triage as run_triage


class CaseState(TypedDict, total=False):
    patient_id: str
    day: str
    events: dict
    results: dict
    days_since_vitals: int
    decision: dict
    weekly: bool


def build_graph(eng, checkpoint_path=None, fault: Optional[Callable[[str], None]] = None, inline_agents=None,
                report_llm="auto", report_root=None):
    """fault(node_name) may raise to simulate a crash. inline_agents: {agent_id: fn(event_row)->dict} used only
    when an agent's stored output is missing (tests / no-process mode). report_llm: "auto" = Gemini if configured
    (llm.model + key), None/False = template mode."""
    from echos.reports.agent import ReportAgent
    from echos.reports.facts import build_day_facts
    from echos.reports.llm import make_llm
    reporter = ReportAgent(eng, make_llm() if report_llm == "auto" else (report_llm or None), root=report_root)
    fault = fault or (lambda node: None)
    cp = checkpoint_path or ROOT / load_config()["checkpoint_db_path"]
    cp = str(cp)
    conn = sqlite3.connect(cp, check_same_thread=False)
    saver = SqliteSaver(conn)

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
                eids = [i for k in kinds for i in st["events"].get(k, [])]
                rows = c.execute(select(S.agent_output).where((S.agent_output.c.agent_id == agent) &
                                                              S.agent_output.c.event_id.in_(eids or [-1]))).fetchall()
                outs = [json.loads(r.result) for r in rows]
                if len(outs) < len(eids) and inline_agents and agent in inline_agents:
                    for e in c.execute(select(S.event_log).where(S.event_log.c.event_id.in_(eids))).fetchall():
                        if e.event_id not in {r.event_id for r in rows}:
                            outs.append(inline_agents[agent](e))
                if eids and len(outs) < len(eids):
                    res[agent] = None  # incomplete -> triage reports a data gap, never NORMAL
                elif outs:
                    res[agent] = max(outs, key=lambda o: o["score"])  # worst case of the day
                else:
                    res[agent] = None if agent != "fall" else {"score": 0.0, "evidence": {}, "flag": False}
        phys = res.get("physiological")
        has_vitals = bool(phys and "current_vs_baseline" in phys["evidence"])
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
        return {"decision": dec.model_dump(mode="json")}

    def route(st: CaseState) -> str:
        return "immediate_alert" if st["decision"]["status"] == "URGENT" else "daily_report"

    def immediate_alert(st: CaseState) -> dict:
        with eng.begin() as c:
            S.insert_ignore(c, S.alerts, patient_id=st["patient_id"], day=st["day"], level="URGENT",
                            reasons=S.j(st["decision"]["reasons"]), created_ts=S.now())
        fault("immediate_alert")  # crash point AFTER the side effect: resume must not duplicate it
        return {}

    def daily_report(st: CaseState) -> dict:  # Report agent: also runs on URGENT days (alerts + times belong in the report)
        facts = build_day_facts(eng, st["patient_id"], st["day"], st["decision"], st["results"])
        out = reporter.daily(facts, defer_llm=True)  # template now, LLM wording later: never blocks alerts / next cases
        with eng.begin() as c:
            c.execute(S.case_state.update().where((S.case_state.c.patient_id == st["patient_id"]) &
                                                  (S.case_state.c.day == st["day"])).values(report=str(out["path"].name)))
        return {}

    def weekly_report(st: CaseState) -> dict:
        reporter.weekly(st["patient_id"], st["day"], defer_llm=True)
        return {}

    def after(st: CaseState) -> str:
        return "weekly_report" if st.get("weekly") else END

    g = StateGraph(CaseState)
    for name, fn in [("load_day", load_day), ("run_agents", run_agents), ("triage", triage_node),
                     ("immediate_alert", immediate_alert), ("daily_report", daily_report), ("weekly_report", weekly_report)]:
        g.add_node(name, fn)
    g.add_edge(START, "load_day")
    g.add_edge("load_day", "run_agents")
    g.add_edge("run_agents", "triage")
    g.add_conditional_edges("triage", route, {"immediate_alert": "immediate_alert", "daily_report": "daily_report"})
    g.add_edge("immediate_alert", "daily_report")
    g.add_conditional_edges("daily_report", after, {"weekly_report": "weekly_report", END: END})
    g.add_edge("weekly_report", END)
    return g.compile(checkpointer=saver), conn


def quality_down(eng) -> bool:
    """Agent 0 is treated as down (its output is optional) when its heartbeat is missing or stale, the same rule the
    workers use before passing events through. The day is then triaged with 'quality unchecked' (DATA_GAP), never NORMAL."""
    with eng.begin() as c:
        hb = c.execute(select(S.agent_heartbeat.c.ts).where(S.agent_heartbeat.c.agent_id == "quality")).first()
    return hb is None or time.time() - hb[0] > 3 * load_config()["runtime"]["heartbeat_s"] + 10


def day_ready(eng, patient_id: str, day: str, quality_optional: bool = False) -> bool:
    """All events of the day exist (the 'day' event is emitted last) and every agent has output for each.
    quality_optional: Agent 0 is down, so its output is not required."""
    with eng.begin() as c:
        evs = c.execute(select(S.event_log.c.event_id, S.event_log.c.kind).where(
            (S.event_log.c.patient_id == patient_id) & (S.event_log.c.day == day))).fetchall()
        if not any(k == "day" for _, k in evs):
            return False
        done = {(r.agent_id, r.event_id) for r in c.execute(select(S.agent_output.c.agent_id, S.agent_output.c.event_id).where(
            (S.agent_output.c.patient_id == patient_id) & (S.agent_output.c.day == day)))}
    return all((a, eid) in done for a, kinds in S.AGENT_KINDS.items() for eid, k in evs
               if k in kinds and not (quality_optional and a == "quality"))


def process_case(graph, patient_id: str, day: str) -> bool:
    """Run (or resume) one patient-day. Returns False if it was already finished."""
    cfg = {"configurable": {"thread_id": f"{patient_id}:{day}"}}
    snap = graph.get_state(cfg)
    if snap.values and not snap.next:
        return False
    graph.invoke(None if snap.next else {"patient_id": patient_id, "day": day}, cfg)
    return True


def run_orchestrator(eng, graph, stop=None, poll_s=0.2, until_done=None, timeout=None) -> int:
    """Process every ready patient-day in chronological order until no unprocessed days remain."""
    t0, n, finished = time.time(), 0, set()
    while not (stop and stop.is_set()):
        producer_done = bool(until_done and until_done())  # read BEFORE the snapshot: a slow pass must not miss late days
        with eng.begin() as c:
            days = c.execute(select(S.event_log.c.patient_id, S.event_log.c.day).where(S.event_log.c.kind == "day")
                             .order_by(S.event_log.c.day, S.event_log.c.patient_id)).fetchall()
        progressed, remaining = False, 0
        q_opt = quality_down(eng)
        for p, d in days:
            if (p, d) in finished:
                continue
            if day_ready(eng, p, d, q_opt):
                n += process_case(graph, p, d)
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
