"""Supervisor: spawns agent workers, detects dead/stalled ones from SQLite heartbeats, restarts them.

Dead  = no heartbeat for `missed_heartbeats` x heartbeat interval (after a startup grace), or
Stall = last_event_id unchanged for `stall_s` while events of the agent's kinds are pending.
A restarted worker resumes from its checkpoint; idempotent processing makes re-reads harmless.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time

from sqlalchemy import func, select

from echos.config import ROOT, load_config
from echos.orchestrator import state as S


class Supervisor:
    def __init__(self, db, agents=("quality", "physiological", "behaviour", "fall"), hb_s=None, ck_s=None, stall_s=None,
                 poll_s=None, missed=None, startup_grace_s=45.0, worker_args=None, fall_model=None, no_restart=()):
        cfg = load_config()
        self.db, self.agents = str(db) if db else None, list(agents)
        self.eng = S.get_engine(db)
        self.hb_s = hb_s or cfg["runtime"]["heartbeat_s"]
        self.ck_s = ck_s or cfg["runtime"]["checkpoint_s"]
        self.stall_s = stall_s or cfg["supervisor"]["stall_s"]
        self.poll_s = poll_s or cfg["supervisor"]["poll_s"]
        self.missed = missed or cfg["supervisor"]["missed_heartbeats"]
        self.grace, self.worker_args, self.fall_model = startup_grace_s, worker_args or {}, fall_model
        self.proc, self.started, self.track = {}, {}, {}
        self.no_restart = set(no_restart)  # fault tests only: agents left dead (permanent outage)
        self.stop = threading.Event()

    # --- process control
    def _spawn(self, agent):
        cmd = [sys.executable, "-W", "ignore", "-m", "echos.orchestrator.runtime", "--agent", agent,
               "--hb", str(self.hb_s), "--ck", str(self.ck_s)]
        if self.db:
            cmd += ["--db", self.db]
        if agent == "fall" and self.fall_model:
            cmd += ["--fall-model", str(self.fall_model)]
        for k, v in self.worker_args.get(agent, {}).items():
            cmd += [f"--{k}", str(v)]
        env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONWARNINGS": "ignore"}
        self.proc[agent] = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.started[agent] = S.now()
        self.track[agent] = {"cursor": None, "since": S.now()}
        return self.proc[agent].pid

    def start_all(self):
        for a in self.agents:
            self._spawn(a)

    def stop_all(self):
        self.stop.set()
        for p in self.proc.values():
            if p.poll() is None:
                p.terminate()
        for p in self.proc.values():
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()

    # --- detection
    def _pending(self, agent, last_id) -> bool:
        with self.eng.begin() as c:
            n = c.execute(select(func.count()).select_from(S.event_log).where(
                (S.event_log.c.event_id > (last_id or 0)) & S.event_log.c.kind.in_(S.AGENT_KINDS[agent]))).scalar()
        return n > 0

    def check_once(self) -> list:
        """Returns [(agent, reason, detected_ts)] for agents restarted in this pass."""
        out, t = [], S.now()
        for a in self.agents:
            with self.eng.begin() as c:
                hb = c.execute(select(S.agent_heartbeat).where(S.agent_heartbeat.c.agent_id == a)).first()
            reason = None
            fresh = hb is not None and hb.ts >= self.started[a]
            if hb is None or not fresh:
                if t - self.started[a] > self.grace:
                    reason = "no_heartbeat_after_start"
            elif hb.status != "stopped":
                if t - hb.ts > self.missed * self.hb_s:
                    reason = "missed_heartbeats"
                else:
                    tr = self.track[a]
                    if tr["cursor"] != hb.last_event_id:
                        tr["cursor"], tr["since"] = hb.last_event_id, t
                    elif t - tr["since"] > self.stall_s and self._pending(a, hb.last_event_id):
                        reason = "stalled_last_event_id"
            if reason and a not in self.no_restart:
                out.append((a, reason, t, self.restart(a, reason, t)))
        return out

    def restart(self, agent, reason, detected_ts):
        p = self.proc.get(agent)
        if p and p.poll() is None:
            p.kill()
            p.wait()
        else:  # not our child (or already gone): make sure the recorded pid is dead
            with self.eng.begin() as c:
                pid = c.execute(select(S.agent_heartbeat.c.pid).where(S.agent_heartbeat.c.agent_id == agent)).scalar()
            if pid:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        self._spawn(agent)
        with self.eng.begin() as c:
            c.execute(S.restart_log.insert().values(agent_id=agent, detected_ts=detected_ts, reason=reason,
                                                    restarted_ts=S.now()))
        return S.now()

    def run(self):
        while not self.stop.wait(self.poll_s):
            self.check_once()
