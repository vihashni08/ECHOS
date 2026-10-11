"""Simulation controller: replays TIHM events into event_log under remote control, and injects SisFall fall windows on request.

Commands come from the `sim_command` table (written by the API, which never loads models) and are consumed here:
  play | pause | speed {eps} | reset | inject_fall {patient_id, file, win_start, subject, activity}
State is published in `sim_state` (status idle|playing|paused|finished, speed, cursor, total, sim_time, message).
SYNTHETIC / SIMULATED data (see echos/sim/replay.py): for demos and systems tests only.

Injected fall: the window is written as a 'sensor' event for the patient's CURRENT (next unclosed) day, at the current simulated time, and that day is
closed immediately (its 'day' event is emitted now instead of at 23:59:59) so the orchestrator raises the alert now. The day-level features come from
the same TIHM patient-day, so closing early only changes WHEN the report is made.
`Runtime` wires the controller to the supervisor (worker processes) and the orchestrator thread: python -m echos.sim.controller [--db PATH]."""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

import pandas as pd
from sqlalchemy import delete, func, select

from echos.config import ROOT, load_config
from echos.orchestrator import state as S

log = logging.getLogger("echos.sim")
WIPE = [S.event_log, S.agent_output, S.case_state, S.alerts, S.outbox, S.daily_facts, S.reports, S.gt_fall, S.agent_checkpoint]


class SimController:
    def __init__(self, eng, events: Optional[list] = None, speed_eps: float = 20.0, n_patients: int = 4, max_days: int = 28, on_reset=None):
        self.eng, self.on_reset = eng, on_reset
        self.n_patients, self.max_days = n_patients, max_days
        self.events = events
        self.done: list = []
        self.cursor, self.speed, self.status, self.sim_time, self.message = 0, float(speed_eps), "idle", None, ""
        self._last_emit = 0.0
        self.stop = threading.Event()
        self._publish()

    # ---- state
    def _publish(self) -> None:
        total = len(self.events) if self.events is not None else None
        with self.eng.begin() as c:
            for k, v in {"status": self.status, "speed": self.speed, "cursor": self.cursor, "total": total, "sim_time": self.sim_time, "message": self.message,
                         "patients": sorted({e["patient_id"] for e in self.events}) if self.events is not None else [], "updated": time.time()}.items():
                S.upsert(c, S.sim_state, ["key"], key=k, value=json.dumps(v))

    def _ensure_events(self) -> None:
        if self.events is None:
            from echos.sim.replay import build_events
            self.events = build_events(self.n_patients, max_days=self.max_days)[0]
        if len(self.done) != len(self.events):
            self.done = [False] * len(self.events)

    # ---- emission
    def _insert(self, e: dict, ts: Optional[str] = None, payload: Optional[dict] = None, gt: Optional[bool] = None) -> int:
        with self.eng.begin() as c:
            eid = c.execute(S.event_log.insert().values(patient_id=e["patient_id"], day=e["day"], ts=ts or e["ts"], kind=e["kind"],
                                                       payload=S.j(payload if payload is not None else e["payload"]))).inserted_primary_key[0]
            if e.get("_fall") if gt is None else gt:
                S.insert_ignore(c, S.gt_fall, event_id=eid, patient_id=e["patient_id"], day=e["day"], ts=ts or e["ts"],
                                subject=(payload or e["payload"]).get("subject"), file=(payload or e["payload"]).get("file"))
        self.sim_time = ts or e["ts"]
        return eid

    def emit_next(self) -> bool:
        self._ensure_events()
        while self.cursor < len(self.events) and self.done[self.cursor]:
            self.cursor += 1
        if self.cursor >= len(self.events):
            self.status, self.message = "finished", "all events emitted"
            return False
        i = self.cursor
        self._insert(self.events[i])
        self.done[i] = True
        self.cursor = i + 1
        return True

    def inject_fall(self, a: dict) -> dict:
        self._ensure_events()
        pid = a["patient_id"]
        j = next((k for k in range(len(self.events)) if not self.done[k] and self.events[k]["patient_id"] == pid and self.events[k]["kind"] == "day"), None)
        if j is None:
            raise ValueError(f"no open day left for patient {pid} (simulation finished for this patient)")
        day_ev = self.events[j]
        d0 = pd.Timestamp(day_ev["day"])
        now = pd.Timestamp(self.sim_time) if self.sim_time else d0 + pd.Timedelta(hours=12)
        ts = now if d0 <= now < d0 + pd.Timedelta(days=1) else d0 + pd.Timedelta(hours=12)
        pl = {"file": a["file"], "win_start": int(a["win_start"]), "subject": a["subject"], "activity": a["activity"], "injected": True}
        eid = self._insert({"patient_id": pid, "day": day_ev["day"], "kind": "sensor", "payload": pl, "ts": ts.isoformat()}, ts=ts.isoformat(), payload=pl, gt=True)
        self._insert(day_ev, ts=(d0 + pd.Timedelta(hours=23, minutes=59, seconds=59)).isoformat())
        self.done[j] = True
        self._publish()
        return {"event_id": eid, "day": day_ev["day"], "ts": ts.isoformat()}

    # ---- commands
    def reset(self) -> None:
        if self.on_reset:
            self.on_reset("before")
        with self.eng.begin() as c:
            for t in WIPE:
                c.execute(delete(t))
        self.cursor, self.sim_time, self.status, self.message = 0, None, "idle", "reset"
        self.done = [False] * len(self.events) if self.events is not None else []
        if self.on_reset:
            self.on_reset("after")

    def handle(self, cmd: str, a: dict) -> object:
        if cmd == "play":
            self._ensure_events()
            if self.status == "finished":
                raise ValueError("simulation finished: reset first")
            if self.cursor == 0 and self.status == "idle":
                with self.eng.begin() as c:
                    if c.execute(select(func.count()).select_from(S.event_log)).scalar():
                        raise ValueError("the store already holds events: reset first")
            self.status, self.message = "playing", ""
        elif cmd == "pause":
            self.status = "paused" if self.status == "playing" else self.status
        elif cmd == "speed":
            v = float(a["speed"])
            if not 0.1 <= v <= 500:
                raise ValueError("speed must be between 0.1 and 500 events per second")
            self.speed = v
        elif cmd == "reset":
            self.reset()
        elif cmd == "inject_fall":
            return self.inject_fall(a)
        else:
            raise ValueError(f"unknown command {cmd}")
        self._publish()
        return None

    def process_commands(self) -> int:
        with self.eng.begin() as c:
            rows = c.execute(select(S.sim_command).where(S.sim_command.c.status == "NEW").order_by(S.sim_command.c.id)).fetchall()
        for r in rows:
            try:
                res = self.handle(r.cmd, json.loads(r.args or "{}"))
                st, out = "DONE", S.j(res)
            except Exception as ex:  # noqa: BLE001 - reported back to the API caller
                st, out = "ERROR", str(ex)[:300]
                log.warning("sim command %s failed: %s", r.cmd, ex)
            with self.eng.begin() as c:
                c.execute(S.sim_command.update().where(S.sim_command.c.id == r.id).values(status=st, result=out))
        return len(rows)

    # ---- loop
    def tick(self, now: Optional[float] = None) -> None:
        self.process_commands()
        now = time.monotonic() if now is None else now
        if self.status == "playing" and now - self._last_emit >= 1.0 / self.speed:
            self._last_emit = now
            if self.emit_next():
                if self.cursor % 5 == 0:
                    self._publish()
            else:
                self._publish()

    def run(self, poll_s: float = 0.05) -> None:
        last_pub = 0.0
        while not self.stop.wait(poll_s):
            try:
                self.tick()
                if time.monotonic() - last_pub > 2.0:     # liveness for the API (sim_state.updated)
                    last_pub = time.monotonic()
                    self._publish()
            except Exception:  # noqa: BLE001 - the controller must survive anything
                log.exception("controller tick failed")


class Runtime:
    """Workers (supervisor) + orchestrator thread + controller thread, for demos."""
    def __init__(self, db=None, n_patients: int = 4, max_days: int = 28, report_llm="auto", weekly_pdf=None, report_root=None):
        from echos.orchestrator.graph import build_graph
        from echos.orchestrator.supervisor import Supervisor
        from echos.sim.replay import train_replay_fall_model
        self.db = db
        self.eng = S.get_engine(db)
        # a run on a specific database keeps its OWN langgraph checkpoint file next to it (never the shared default one)
        self.cp = Path(db).with_suffix(".cp.sqlite") if db else ROOT / load_config()["checkpoint_db_path"]
        self.report_llm, self.weekly_pdf, self.report_root = report_llm, weekly_pdf, report_root
        self._build_graph = build_graph
        self.sup = Supervisor(db, fall_model=train_replay_fall_model())
        self.ctl = SimController(self.eng, n_patients=n_patients, max_days=max_days, on_reset=self._on_reset)
        self.stop_orch = threading.Event()
        self.orch = None
        self.graph = self.conn = None

    def _start_orch(self):
        from echos.orchestrator.graph import run_orchestrator
        self.stop_orch = threading.Event()
        self.graph, self.conn = self._build_graph(self.eng, self.cp, report_llm=self.report_llm, weekly_pdf=self.weekly_pdf, report_root=self.report_root)
        self.orch = threading.Thread(target=run_orchestrator, args=(self.eng, self.graph), kwargs={"stop": self.stop_orch, "poll_s": 0.2}, daemon=True)
        self.orch.start()

    def _on_reset(self, phase: str):
        if phase == "before":
            self.stop_orch.set()
            if self.orch:
                self.orch.join(30)
            if self.conn:
                self.conn.close()
            try:
                c = sqlite3.connect(self.cp)
                for t in ("checkpoints", "writes"):
                    c.execute(f"DELETE FROM {t}")
                c.commit()
                c.close()
            except sqlite3.Error:
                pass
        else:
            for a in self.sup.agents:      # workers resume from the (now cleared) checkpoints: restart so their in-memory cursors reset
                self.sup.restart(a, "sim_reset", S.now())
            self._start_orch()

    def start(self):
        self.ctl._ensure_events()
        self.ctl._publish()
        self.sup.start_all()
        threading.Thread(target=self.sup.run, daemon=True).start()
        self._start_orch()
        threading.Thread(target=self.ctl.run, daemon=True).start()

    def close(self):
        self.ctl.stop.set()
        self.stop_orch.set()
        self.sup.stop_all()


def main():
    import os
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("ECHOS_DB"))
    ap.add_argument("--patients", type=int, default=4)
    ap.add_argument("--days", type=int, default=28)
    ap.add_argument("--no-llm", action="store_true", help="template reports only (no Gemini calls)")
    ap.add_argument("--no-pubmed", action="store_true", help="no live PubMed (local guideline index only)")
    a = ap.parse_args()
    if a.no_llm or a.no_pubmed:
        os.environ["ECHOS_PUBMED"] = "off"
    rt = Runtime(a.db, a.patients, a.days, report_root=os.environ.get("ECHOS_REPORT_ROOT"), report_llm=None if (a.no_llm or os.environ.get("ECHOS_REPORT_LLM") == "off") else "auto")
    rt.start()
    print("runtime started (workers + orchestrator + controller); Ctrl-C to stop", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        rt.close()


if __name__ == "__main__":
    main()
