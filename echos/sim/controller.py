"""Replay controller: replays the v2 TIHM events (echos/sim/replay_v2.py) into event_log under remote control.

Commands come from the `sim_command` table (written by the API, which never loads models) and are consumed here:
  play | pause | speed {eps} | reset | inject_reading {patient_id, sys, dia, hr, temp}
State is published in `sim_state` (status idle|playing|paused|finished, speed, cursor, total, sim_time, message).
inject_reading is the only invented data: one demo reading for a replay patient at the current simulated time (payload marked injected).
The runtime that owns the controller and the agent processes is echos.team.runtime (python -m echos.sim.controller starts it)."""
from __future__ import annotations

import argparse
import json
import logging
import threading
import time
from typing import Optional

import pandas as pd
from sqlalchemy import delete, func, select

from echos.orchestrator import state as S

log = logging.getLogger("echos.sim")
WIPE = [S.event_log, S.agent_output, S.case_state, S.alerts, S.outbox, S.agent_checkpoint, S.alert_state, S.care_task, S.agent_message,
        S.patient_agent, S.review_list, S.agent_state]


class SimController:
    def __init__(self, eng, events: Optional[list] = None, speed_eps: float = 20.0, n_patients: int = 4, max_days: int = 28, on_reset=None,
                 builder=None, extra_wipe=()):
        self.eng, self.on_reset = eng, on_reset
        self.builder, self.wipe = builder, list(dict.fromkeys(list(WIPE) + list(extra_wipe)))   # builder: () -> events (echos/sim/replay_v2.py)
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
            if self.builder is None:
                from echos.sim.replay_v2 import build_events
                self.builder = lambda: build_events(self.n_patients, self.max_days)
            self.events = self.builder()
        if len(self.done) != len(self.events):
            self.done = [False] * len(self.events)

    def resume(self) -> int:
        """After a runtime restart (watchdog): the replay events are deterministic, so the events already in event_log are the first N of this
        replay. Continue after them, paused (or finished), instead of starting again from event 0. Injected readings are not replay events."""
        self._ensure_events()
        with self.eng.begin() as c:
            n = c.execute(select(func.count()).select_from(S.event_log).where(S.event_log.c.kind != "reading")).scalar() or 0
            last = c.execute(select(S.event_log.c.ts).order_by(S.event_log.c.event_id.desc()).limit(1)).scalar()
        if n:
            n = min(n, len(self.events))
            self.done = [True] * n + [False] * (len(self.events) - n)
            self.cursor, self.sim_time = n, last
            self.status = "finished" if n >= len(self.events) else "paused"
            self.message = "resumed after a restart" if self.status == "paused" else "all events emitted"
        self._publish()
        return n

    # ---- emission
    def _insert(self, e: dict, ts: Optional[str] = None, payload: Optional[dict] = None, gt: Optional[bool] = None) -> int:
        with self.eng.begin() as c:
            eid = c.execute(S.event_log.insert().values(patient_id=e["patient_id"], day=e["day"], ts=ts or e["ts"], kind=e["kind"],
                                                       payload=S.j(payload if payload is not None else e["payload"]))).inserted_primary_key[0]
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

    def inject_reading(self, a: dict) -> dict:
        """Demo / live: one vitals reading for a patient at the current simulated time (kind 'reading'): the Patient Monitor checks it at once."""
        self._ensure_events()
        pid = a["patient_id"]
        if pid not in {e["patient_id"] for e in self.events}:
            raise ValueError(f"patient {pid} is not in this replay")
        now = pd.Timestamp(self.sim_time) if self.sim_time else pd.Timestamp(next(e["ts"] for e in self.events if e["patient_id"] == pid))
        pl = {k: float(a[k]) for k in ("sys", "dia", "hr", "temp") if a.get(k) is not None}
        pl["injected"] = True
        eid = self._insert({"patient_id": pid, "day": now.strftime("%Y-%m-%d"), "kind": "reading", "payload": pl, "ts": now.isoformat()},
                           ts=now.isoformat(), payload=pl, gt=False)
        self._publish()
        return {"event_id": eid, "day": now.strftime("%Y-%m-%d"), "ts": now.isoformat()}

    # ---- commands
    def reset(self) -> None:
        if self.on_reset:
            self.on_reset("before")
        with self.eng.begin() as c:
            for t in self.wipe:
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
        elif cmd == "inject_reading":
            return self.inject_reading(a)
        else:
            raise ValueError(f"unknown command {cmd}")
        self._publish()
        return None

    def process_commands(self) -> int:
        with self.eng.begin() as c:
            rows = c.execute(select(S.sim_command).where((S.sim_command.c.status == "NEW") & ~S.sim_command.c.cmd.like("emu_%")).order_by(S.sim_command.c.id)).fetchall()
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


def main():
    from echos.team.runtime import main as team_main
    return team_main()


if __name__ == "__main__":
    main()
