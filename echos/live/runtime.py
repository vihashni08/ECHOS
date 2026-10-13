"""Live runtime: LiveProcessor (detectors from events, rolling triage) + Care Coordinator + (optionally) the labelled device emulator.
python -m echos.live.runtime --db data/echos_live.db [--no-llm] [--no-emulator]   (scripts/run_api.sh --mode live does this)"""
from __future__ import annotations

import argparse
import os
import threading
import time
from pathlib import Path

from echos.config import ROOT, load_config
from echos.orchestrator import state as S


class LiveRuntime:
    def __init__(self, db=None, report_llm="auto", report_root=None, emulator: bool = True, dispatcher=None):
        from echos.alerts.dispatcher import AlertDispatcher
        from echos.alerts.mailer import make_mailer
        from echos.live.emulator import Emulator
        from echos.live.processor import LiveProcessor
        from echos.reports.agent import ReportAgent
        from echos.reports.llm import make_llm
        from echos.reports.llm_budget import make_budget
        from echos.sim.replay import train_replay_fall_model
        from echos.team.coordinator import CareCoordinator
        self.eng = S.get_engine(db)
        auto = report_llm == "auto"
        self.reporter = ReportAgent(self.eng, make_llm() if auto else (report_llm or None), root=report_root, budget=make_budget() if auto else None)
        if dispatcher is None:
            dispatcher = AlertDispatcher(self.eng, make_mailer())
            dispatcher.recover()
            dispatcher.start_sweeper()
        self.dispatcher = dispatcher
        self.proc = LiveProcessor(self.eng, dispatcher, self.reporter, fall_model=train_replay_fall_model())
        self.coord = CareCoordinator(self.eng, dispatcher)
        self.emu = Emulator(self.eng) if emulator else None
        self.threads = []

    def start(self):
        with self.eng.begin() as c:
            S.upsert(c, S.sim_state, ["key"], key="mode", value='"emulator"' if self.emu else '"live"')
        self.coord.start()
        for fn in ([self.proc.run] + ([self.emu.run] if self.emu else [])):
            t = threading.Thread(target=fn, daemon=True)
            t.start()
            self.threads.append(t)

    def close(self):
        self.proc.stop.set()
        if self.emu:
            self.emu.stop.set()
        self.coord.stop()
        self.dispatcher.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("ECHOS_DB"))
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--no-emulator", action="store_true", help="real sources only: events arrive through POST /api/ingest/events")
    a = ap.parse_args()
    if a.no_llm:
        os.environ["ECHOS_PUBMED"] = "off"
    rt = LiveRuntime(a.db, report_llm=None if (a.no_llm or os.environ.get("ECHOS_REPORT_LLM") == "off") else "auto",
                     report_root=os.environ.get("ECHOS_REPORT_ROOT"), emulator=not a.no_emulator)
    rt.start()
    print("live runtime started (processor + coordinator" + ("" if a.no_emulator else " + emulator") + "); Ctrl-C to stop", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        rt.close()


if __name__ == "__main__":
    main()
