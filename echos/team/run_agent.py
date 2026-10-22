"""Run ONE v2 agent as its own process (the supervisor starts three of these):

    python -m echos.team.run_agent --agent patient_monitor|review_coordinator|care [--db PATH]
"""
from __future__ import annotations

import argparse
import logging
import signal

from echos.orchestrator import state as S

AGENTS = ("patient_monitor", "review_coordinator", "care")


def build(name: str, eng, hb_s=None):
    if name == "patient_monitor":
        from echos.team.patient_monitor import PatientMonitorAgent
        return PatientMonitorAgent(eng, hb_s=hb_s)
    if name == "review_coordinator":
        from echos.team.review_coordinator import ReviewCoordinatorAgent
        return ReviewCoordinatorAgent(eng, hb_s=hb_s)
    if name == "care":
        from echos.alerts.dispatcher import AlertDispatcher
        from echos.alerts.mailer import make_mailer
        from echos.team.care_agent import CareAgent
        disp = AlertDispatcher(eng, make_mailer())
        disp.recover()                 # retries left in the outbox by a previous process
        disp.start_sweeper()
        return CareAgent(eng, disp, hb_s=hb_s)
    raise ValueError(f"unknown agent {name!r} (choose from {AGENTS})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True, choices=AGENTS)
    ap.add_argument("--db", default=None)
    ap.add_argument("--hb", type=float, default=None)
    a = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format=f"%(asctime)s {a.agent} %(levelname)s %(message)s")
    agent = build(a.agent, S.get_engine(a.db), a.hb)
    signal.signal(signal.SIGTERM, lambda *_: agent.stop.set())
    agent.run()


if __name__ == "__main__":
    main()
