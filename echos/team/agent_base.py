"""What every ECHOS v2 agent process shares: a heartbeat (so the supervisor can see it is alive and restart it), a run loop that calls step(),
a cursor over event_log kept in agent_checkpoint (so a restart resumes where it stopped), and a small key/value memory in agent_state.

The three agents never call each other: they read the event log and talk over the SQLite bus (echos/team/bus.py)."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Iterable, Optional

from sqlalchemy import select

from echos.config import load_config
from echos.orchestrator import state as S

log = logging.getLogger("echos.agents")


class AgentBase:
    AGENT = "agent"
    KINDS: tuple = ()          # event_log kinds this agent consumes (empty = bus only)

    def __init__(self, eng, hb_s: Optional[float] = None, poll_s: Optional[float] = None, batch: Optional[int] = None):
        rt = load_config()["runtime"]
        self.eng = eng
        self.hb_s = hb_s or rt["heartbeat_s"]
        self.poll_s = poll_s or rt["poll_s"]
        self.batch = batch or rt["batch"]
        self.stop = threading.Event()
        self.status = "starting"
        self.cursor = self.load_cursor()
        self._wait_t0: dict = {}

    # ---- liveness
    def heartbeat(self) -> None:
        with self.eng.begin() as c:
            S.upsert(c, S.agent_heartbeat, ["agent_id"], agent_id=self.AGENT, ts=S.now(), last_event_id=self.cursor, status=self.status, pid=os.getpid())

    def _hb_loop(self) -> None:
        while not self.stop.wait(self.hb_s):
            try:
                self.heartbeat()
            except Exception:  # noqa: BLE001
                log.exception("%s heartbeat failed", self.AGENT)

    def peer_down(self, agent: str) -> bool:
        """Another agent's heartbeat is missing or stale (the supervisor is restarting it): worth waiting for rather than working around."""
        with self.eng.begin() as c:
            ts = c.execute(select(S.agent_heartbeat.c.ts).where(S.agent_heartbeat.c.agent_id == agent)).scalar()
        return ts is None or time.time() - ts > 3 * self.hb_s

    def waited_enough(self, key, soft_s: float, hard_s: float, peer: str) -> bool:
        """Wait-or-proceed rule shared by the agents: keep waiting while `peer` is down (up to hard_s), otherwise up to soft_s."""
        t0 = self._wait_t0.setdefault(key, time.time())
        waited = time.time() - t0
        if waited >= hard_s:
            return True
        return waited >= soft_s and not self.peer_down(peer)

    def done_waiting(self, key) -> None:
        self._wait_t0.pop(key, None)

    # ---- event log cursor
    def load_cursor(self, key: Optional[str] = None) -> int:
        with self.eng.begin() as c:
            v = c.execute(select(S.agent_checkpoint.c.last_event_id).where(S.agent_checkpoint.c.agent_id == (key or self.AGENT))).scalar()
        return int(v or 0)

    def save_cursor(self, value: int, key: Optional[str] = None) -> None:
        with self.eng.begin() as c:
            S.upsert(c, S.agent_checkpoint, ["agent_id"], agent_id=key or self.AGENT, last_event_id=int(value), ts=S.now())

    def next_events(self, kinds: Iterable[str], after: int, limit: Optional[int] = None) -> list:
        with self.eng.begin() as c:
            return c.execute(select(S.event_log).where((S.event_log.c.event_id > after) & S.event_log.c.kind.in_(list(kinds)))
                             .order_by(S.event_log.c.event_id).limit(limit or self.batch)).fetchall()

    # ---- small persistent memory
    def remember(self, key: str, value) -> None:
        with self.eng.begin() as c:
            S.upsert(c, S.agent_state, ["agent_id", "key"], agent_id=self.AGENT, key=key, value=json.dumps(value, default=str))

    def recall(self, key: str, default=None):
        with self.eng.begin() as c:
            v = c.execute(select(S.agent_state.c.value).where((S.agent_state.c.agent_id == self.AGENT) & (S.agent_state.c.key == key))).scalar()
        return default if v is None else json.loads(v)

    # ---- loop
    def step(self) -> int:     # pragma: no cover - implemented by each agent
        raise NotImplementedError

    def run(self) -> None:
        self.heartbeat()
        threading.Thread(target=self._hb_loop, daemon=True, name=f"{self.AGENT}-hb").start()
        self.status = "running"
        self.heartbeat()
        while not self.stop.is_set():
            try:
                n = self.step()
            except Exception:  # noqa: BLE001 - an agent survives a bad pass; the next pass retries (all work is idempotent)
                log.exception("%s step failed", self.AGENT)
                n = 0
            if not n:
                self.stop.wait(self.poll_s)
        self.status = "stopped"
        self.heartbeat()

    def run_until_idle(self, max_steps: int = 10_000) -> int:
        """Tests / batch runs: step until nothing is left to do."""
        total = 0
        for _ in range(max_steps):
            n = self.step()
            total += n
            if not n:
                break
        return total
