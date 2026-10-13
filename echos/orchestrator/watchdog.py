"""Watchdog: keeps the RUNTIME process alive (the supervisor, orchestrator / live processor, coordinator and emulator all live in it).
The supervisor restarts dead detector workers; this restarts the process that holds the supervisor, so the supervisor is no longer a single point of
failure for restarts. It restarts the child when it exits or when its liveness (sim_state.updated, written every second or two) goes stale, with
exponential backoff, and records every restart in restart_log (agent 'runtime').  Its own heartbeat is agent 'watchdog'.
Usage: python -m echos.orchestrator.watchdog --db data/echos.db [--stale-s 30] -- python -m echos.live.runtime --db data/echos.db"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from typing import Optional

from sqlalchemy import select

from echos.config import ROOT
from echos.orchestrator import state as S

log = logging.getLogger("echos.watchdog")


class Watchdog:
    def __init__(self, cmd: list, db=None, stale_s: float = 30.0, grace_s: float = 45.0, poll_s: float = 1.0, backoff_s=(1.0, 2.0, 5.0, 10.0, 30.0), env: Optional[dict] = None):
        self.cmd, self.eng, self.stale_s, self.grace_s, self.poll_s, self.backoff = cmd, S.get_engine(db), stale_s, grace_s, poll_s, list(backoff_s)
        self.env = {**os.environ, "PYTHONPATH": str(ROOT), **(env or {})}
        self.proc: Optional[subprocess.Popen] = None
        self.started = 0.0
        self.fails = 0
        self.restarts = 0
        self.stop = threading.Event()

    def _spawn(self):
        self.proc = subprocess.Popen(self.cmd, cwd=ROOT, env=self.env)
        self.started = time.time()

    def _alive_ts(self) -> Optional[float]:
        with self.eng.begin() as c:
            v = c.execute(select(S.sim_state.c.value).where(S.sim_state.c.key == "updated")).scalar()
        return float(json.loads(v)) if v else None

    def _heartbeat(self):
        with self.eng.begin() as c:
            S.upsert(c, S.agent_heartbeat, ["agent_id"], agent_id="watchdog", ts=S.now(), last_event_id=self.restarts, status="running", pid=os.getpid())

    def _restart(self, reason: str):
        t = time.time()
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        delay = self.backoff[min(self.fails, len(self.backoff) - 1)]
        self.fails += 1
        log.warning("runtime %s: restarting in %.0f s", reason, delay)
        if self.stop.wait(delay):
            return
        self._spawn()
        self.restarts += 1
        with self.eng.begin() as c:
            c.execute(S.restart_log.insert().values(agent_id="runtime", detected_ts=t, reason=reason, restarted_ts=time.time()))

    def check_once(self) -> Optional[str]:
        """One pass: returns the restart reason, or None."""
        if self.proc is None:
            self._spawn()
            return None
        rc = self.proc.poll()
        if rc is not None:
            self._restart(f"exited_{rc}")
            return f"exited_{rc}"
        up = self._alive_ts()
        if time.time() - self.started > self.grace_s and (up is None or time.time() - up > self.stale_s):
            self._restart("stale_liveness")
            return "stale_liveness"
        if up and up > self.started and time.time() - self.started > 60:
            self.fails = 0                                   # it has been healthy for a minute: reset the backoff
        return None

    def run(self):
        while not self.stop.is_set():
            try:
                self._heartbeat()
                self.check_once()
            except Exception:  # noqa: BLE001 - the watchdog itself must not die
                log.exception("watchdog pass failed")
            self.stop.wait(self.poll_s)
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        with self.eng.begin() as c:
            S.upsert(c, S.agent_heartbeat, ["agent_id"], agent_id="watchdog", ts=S.now(), last_event_id=self.restarts, status="stopped", pid=os.getpid())


def main():
    argv = sys.argv[1:]
    cmd = argv[argv.index("--") + 1:] if "--" in argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("ECHOS_DB"))
    ap.add_argument("--stale-s", type=float, default=30.0)
    a = ap.parse_args(argv[:argv.index("--")] if "--" in argv else argv)
    if not cmd:
        ap.error("give the runtime command after --")
    w = Watchdog(cmd, a.db, stale_s=a.stale_s)
    signal.signal(signal.SIGTERM, lambda *_: w.stop.set())
    try:
        w.run()
    except KeyboardInterrupt:
        w.stop.set()
        w.run()


if __name__ == "__main__":
    main()
