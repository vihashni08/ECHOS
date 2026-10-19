"""Agent worker process: consumes events from event_log, writes heartbeats + checkpoints, idempotent by event_id.

Run as a separate process:  python -m echos.orchestrator.runtime --agent fall --db data/echos.db
"""
from __future__ import annotations

import argparse
import os
import threading
import time
from collections import OrderedDict

import numpy as np
from sqlalchemy import and_, select

from echos.config import load_config
from echos.orchestrator import state as S


class Handlers:
    """Plain functions turning an event row into an AgentResult."""

    def __init__(self, fall_model=None):
        self.fall_model, self._files = fall_model, OrderedDict()

    def _load(self, path):
        from echos.data import sisfall as sf
        if path not in self._files:
            self._files[path] = sf.load_file(path)
            while len(self._files) > 8:
                self._files.popitem(last=False)
        return self._files[path]

    def __call__(self, agent_id: str, ev) -> dict:
        if agent_id == "quality":
            from echos.agents import data_quality as m
            return m.predict(ev.patient_id, ev.day).model_dump(mode="json")  # never raises: pass-through + 'unchecked'
        if agent_id == "health_monitoring":
            from echos.agents import health_monitoring as m
            try:
                return m.predict(ev.patient_id, ev.day).model_dump(mode="json")
            except Exception as ex:  # noqa: BLE001 - never block the pipeline: fixed vitals thresholds, 'monitoring degraded'
                return m.fallback_predict(ev.patient_id, ev.day, type(ex).__name__).model_dump(mode="json")
        if agent_id == "fall":
            import json
            import pandas as pd
            from echos.agents import fall
            if self.fall_model and not fall._cache:
                fall.use_model(self.fall_model)
            a3 = load_config()["agent3"]
            fs, n = a3["fs"], int(a3["window_s"] * a3["fs"])
            pl = json.loads(ev.payload)
            a, s = self._load(pl["file"]), pl["win_start"]
            w = a[s:s + n]
            pk = int(np.argmax(np.sqrt((w ** 2).sum(axis=1))))   # inactivity is measured from the window's OWN max sample (as in training)
            r = fall.predict_window(w, fs, following=a[s + pk + fs:], patient_id=ev.patient_id,
                                    day=pd.Timestamp(ev.day).date())
            r.evidence.update({"event_id": ev.event_id, "ts": ev.ts})
            return r.model_dump(mode="json")
        raise ValueError(agent_id)


class AgentWorker:
    def __init__(self, agent_id, db, hb_s=None, ck_s=None, poll_s=None, batch=None, process_delay_s=0.0,
                 hang_after=None, fall_model=None):
        rt = load_config()["runtime"]
        self.id, self.eng = agent_id, S.get_engine(db)
        self.hb_s, self.ck_s = hb_s or rt["heartbeat_s"], ck_s or rt["checkpoint_s"]
        self.poll_s, self.batch = poll_s or rt["poll_s"], batch or rt["batch"]
        self.delay, self.hang_after = process_delay_s, hang_after
        self.kinds, self.handlers = S.AGENT_KINDS[agent_id], Handlers(fall_model)
        self.stop, self.cursor, self.status, self.n_done = threading.Event(), 0, "starting", 0
        self.t_start = time.time()

    # --- bookkeeping
    def _heartbeat(self):
        with self.eng.begin() as c:
            S.upsert(c, S.agent_heartbeat, ["agent_id"], agent_id=self.id, ts=S.now(), last_event_id=self.cursor,
                     status=self.status, pid=os.getpid())

    def _checkpoint(self):
        with self.eng.begin() as c:
            S.upsert(c, S.agent_checkpoint, ["agent_id"], agent_id=self.id, last_event_id=self.cursor, ts=S.now())

    def _hb_loop(self):
        while not self.stop.wait(self.hb_s):
            self._heartbeat()

    def _quality_ready(self, ev) -> bool:
        """True when Agent 0 has output for this event. If Agent 0 looks dead (stale heartbeat, or none within 15 s of
        this worker's start, e.g. a run without Agent 0) the event passes through so the pipeline never blocks on it."""
        with self.eng.begin() as c:
            done = c.execute(select(S.agent_output.c.event_id).where(and_(S.agent_output.c.event_id == ev.event_id,
                                                                          S.agent_output.c.agent_id == "quality"))).first()
            hb = c.execute(select(S.agent_heartbeat.c.ts).where(S.agent_heartbeat.c.agent_id == "quality")).first()
        if done:
            return True
        if hb is None:
            return time.time() - self.t_start > 15
        return time.time() - hb[0] > 3 * self.hb_s + 10

    # --- main loop
    def run(self):
        with self.eng.begin() as c:  # resume from checkpoint
            row = c.execute(select(S.agent_checkpoint.c.last_event_id).where(S.agent_checkpoint.c.agent_id == self.id)).first()
        self.cursor = row[0] if row else 0
        self._heartbeat()
        threading.Thread(target=self._hb_loop, daemon=True).start()
        last_ck = time.time()
        self.status = "running"
        self._heartbeat()
        while not self.stop.is_set():
            with self.eng.begin() as c:
                evs = c.execute(select(S.event_log).where(and_(S.event_log.c.event_id > self.cursor,
                                                              S.event_log.c.kind.in_(self.kinds)))
                                .order_by(S.event_log.c.event_id).limit(self.batch)).fetchall()
            for ev in evs:
                if self.id in S.AFTER_QUALITY and not self._quality_ready(ev):
                    break  # keep event order: wait for Agent 0 (bounded by its heartbeat, see _quality_ready)
                if self.hang_after is not None and self.n_done >= self.hang_after:
                    threading.Event().wait()  # simulated hang: heartbeats continue, progress stops
                self._process(ev)
                if time.time() - last_ck >= self.ck_s:
                    self._checkpoint()
                    last_ck = time.time()
            if not evs:
                self.stop.wait(self.poll_s)
                if time.time() - last_ck >= self.ck_s:
                    self._checkpoint()
                    last_ck = time.time()
        self.status = "stopped"
        self._checkpoint()
        self._heartbeat()

    def _process(self, ev):
        with self.eng.begin() as c:
            done = c.execute(select(S.agent_output.c.event_id).where(
                and_(S.agent_output.c.event_id == ev.event_id, S.agent_output.c.agent_id == self.id))).first()
        if not done:  # idempotent: reprocessing after a restart never recomputes or double-writes
            res = self.handlers(self.id, ev)
            if self.delay:
                time.sleep(self.delay)
            with self.eng.begin() as c:
                S.insert_ignore(c, S.agent_output, event_id=ev.event_id, agent_id=self.id, patient_id=ev.patient_id,
                                day=ev.day, result=S.j(res), done_ts=S.now())
        self.cursor = ev.event_id
        self.n_done += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True)
    ap.add_argument("--db", default=None)
    ap.add_argument("--hb", type=float, default=None)
    ap.add_argument("--ck", type=float, default=None)
    ap.add_argument("--delay", type=float, default=0.0)
    ap.add_argument("--hang-after", type=int, default=None)
    ap.add_argument("--fall-model", default=None)
    a = ap.parse_args()
    AgentWorker(a.agent, a.db, a.hb, a.ck, process_delay_s=a.delay, hang_after=a.hang_after, fall_model=a.fall_model).run()


if __name__ == "__main__":
    main()
