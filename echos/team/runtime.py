"""ECHOS v2 runtime: the replay controller + the three agent processes under a supervisor.

    python -m echos.team.runtime [--db PATH] [--patients 8] [--days 28]

The supervisor (echos/orchestrator/supervisor.py) restarts an agent process whose heartbeat stops or whose cursor stalls while work is
pending; each agent resumes from its checkpoint and its saved memory, and all work is idempotent. The API controls the replay through the
sim_command table (play / pause / speed / reset / inject_reading)."""
from __future__ import annotations

import argparse
import logging
import os
import threading
import time

from echos.orchestrator import state as S
from echos.orchestrator.supervisor import Supervisor
from echos.sim.controller import SimController

log = logging.getLogger("echos.team.runtime")


V2_WIPE = [S.patient_agent, S.review_list, S.agent_state]


def build_v2_events(n_patients: int, max_days: int) -> list:
    from echos.sim.replay_v2 import build_events
    return build_events(n_patients, max_days)


class TeamRuntime:
    def __init__(self, db=None, n_patients: int = 8, max_days: int = 28):
        self.db = db
        self.eng = S.get_engine(db)
        self.sup = Supervisor(db)
        self.ctl = SimController(self.eng, n_patients=n_patients, max_days=max_days, on_reset=self._on_reset,
                                 builder=lambda: build_v2_events(n_patients, max_days), extra_wipe=V2_WIPE)

    def _on_reset(self, phase: str):
        if phase == "after":
            for a in self.sup.agents:          # in-memory cursors and memories reset with the tables
                self.sup.restart(a, "sim_reset", S.now())

    def start(self):
        self.ctl.resume()                      # a restarted runtime continues the replay where the database says it was
        self.sup.start_all()
        threading.Thread(target=self.sup.run, daemon=True).start()
        threading.Thread(target=self.ctl.run, daemon=True).start()

    def close(self):
        self.ctl.stop.set()
        self.sup.stop_all()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("ECHOS_DB"))
    ap.add_argument("--patients", type=int, default=8)
    ap.add_argument("--days", type=int, default=28)
    a, _unknown = ap.parse_known_args()     # run_api.sh also passes v1 flags (--no-llm ...): ignored here
    logging.basicConfig(level=logging.WARNING)
    rt = TeamRuntime(a.db, a.patients, a.days)
    rt.start()
    print("ECHOS v2 runtime started (patient_monitor, review_coordinator, care + replay controller); Ctrl-C to stop", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        rt.close()


if __name__ == "__main__":
    main()
