"""Device emulator (LABELLED: not a real device). Streams real TIHM raw events (room-sensor and vitals readings) for chosen patients in wall-clock
time, through the same ingestion function the API uses, plus device heartbeats; falls are injected on demand from SisFall windows.
speed = simulated seconds per wall second (config live.emulator.speed). The patient's clock is the data time; wall time only drives the pace."""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import select

from echos.config import load_config
from echos.live import store as LS
from echos.live.schemas import ROOMS, VITAL_TYPES, IngestEvent
from echos.orchestrator import state as S

log = logging.getLogger("echos.emulator")
ID = "emulator"


def _state(eng, **kv):
    with eng.begin() as c:
        for k, v in kv.items():
            S.upsert(c, S.sim_state, ["key"], key=f"emu_{k}", value=json.dumps(v))


def read_state(eng) -> dict:
    with eng.begin() as c:
        return {r.key[4:]: json.loads(r.value) for r in c.execute(select(S.sim_state).where(S.sim_state.c.key.like("emu_%")))}


class Emulator:
    def __init__(self, eng, cfg: Optional[dict] = None, clock=time.time):
        self.eng, self.clock = eng, clock
        self.ec = {**load_config().get("live", {}).get("emulator", {}), **(cfg or {})}
        self.speed = float(self.ec.get("speed", 3600))
        self.status = "idle"
        self.events: list = []            # [(ts Timestamp, patient, dict)] sorted
        self.cursor = 0
        self.sim_now: Optional[pd.Timestamp] = None
        self.patients: list = []
        self._last_wall: Optional[float] = None
        self._hb_next: dict = {}
        self.stop = threading.Event()

    # ------------------------------------------------------------ setup
    def load(self, patients: Optional[list] = None, start_day: Optional[str] = None, n_days: Optional[int] = None, n_patients: Optional[int] = None):
        from echos.data import loaders
        from echos.data.patient_day import load_patient_day
        from echos.sim.replay import pick_patients
        start = pd.Timestamp(start_day or self.ec.get("start_day", "2019-05-13")).normalize()
        n_days = int(n_days or self.ec.get("n_days", 14))
        end = start + pd.Timedelta(days=n_days)
        pdy = load_patient_day()
        pats = list(patients) if patients else pick_patients(pdy[pdy.day < start], int(n_patients or self.ec.get("n_patients", 4)))
        act, phys = loaders.load_activity(), loaders.load_physiology()
        act = act[act.patient_id.isin(pats) & (act.date >= start) & (act.date < end)]
        phys = phys[phys.patient_id.isin(pats) & phys.device_type.isin(VITAL_TYPES) & (phys.date >= start) & (phys.date < end)]
        ev = [(r.date, r.patient_id, {"kind": "activity", "room": r.location_name}) for r in act.itertuples() if r.location_name in ROOMS]
        ev += [(r.date, r.patient_id, {"kind": "vitals", "device_type": r.device_type, "value": float(r.value), "unit": getattr(r, "unit", None)}) for r in phys.itertuples()]
        step = pd.Timedelta(minutes=int(self.ec.get("heartbeat_min", 15)))
        t = start
        while t < end:                                                    # device heartbeats: the patient's clock advances even when nobody moves
            ev += [(t, p, {"kind": "heartbeat"}) for p in pats]
            t += step
        ev.sort(key=lambda x: (x[0], x[1], x[2]["kind"]))
        self.events, self.patients, self.cursor, self.sim_now, self.start, self.end = ev, pats, 0, start, start, end
        _state(self.eng, patients=pats, start=str(start.date()), end=str(end.date()), total=len(ev), speed=self.speed, status="idle", sim_now=str(start), cursor=0, labelled="EMULATOR: replays real TIHM raw events in wall-clock time; not a real device")
        return pats

    # ------------------------------------------------------------ pace
    def tick(self, wall: Optional[float] = None) -> int:
        """Emit every event whose data time has been reached. Returns events written."""
        wall = self.clock() if wall is None else wall
        if self.status != "playing":
            self._last_wall = wall
            return 0
        if self._last_wall is not None:
            self.sim_now = self.sim_now + pd.Timedelta(seconds=(wall - self._last_wall) * self.speed)
        self._last_wall = wall
        batch = []
        while self.cursor < len(self.events) and self.events[self.cursor][0] <= self.sim_now and len(batch) < 400:
            ts, pid, d = self.events[self.cursor]
            batch.append(IngestEvent(patient_id=pid, device_id=f"{ID}-{pid[:6]}", ts=ts.to_pydatetime(), source="emulator", **d))
            self.cursor += 1
        if batch:
            LS.write_events(self.eng, batch)
        if self.cursor >= len(self.events):
            self.status = "finished"
        _state(self.eng, status=self.status, sim_now=str(self.sim_now), cursor=self.cursor)
        return len(batch)

    # ------------------------------------------------------------ commands
    def inject_fall(self, a: dict) -> dict:
        """a: patient_id + a SisFall window (file, win_start, subject, activity) picked by the API. Writes ONE accelerometer event at the current data time."""
        from echos.data import sisfall as sf
        pid = a["patient_id"]
        if pid not in self.patients:
            raise ValueError(f"{pid} is not part of this emulation")
        if self.sim_now is None:
            raise ValueError("emulator not loaded")
        cfg = load_config()["agent3"]
        fs, n = int(cfg["fs"]), int(cfg["window_s"] * int(cfg["fs"]))
        arr = sf.load_file(a["file"])
        s = int(a["win_start"])
        samples = arr[s:s + n + 6 * fs]
        ts = self.sim_now
        ev = IngestEvent(patient_id=pid, device_id=f"{ID}-{pid[:6]}", ts=ts.to_pydatetime(), kind="accel", fs=fs, samples=np.round(samples, 4).tolist(), source="emulator")
        r = LS.write_events(self.eng, [ev])
        with self.eng.begin() as c:
            eid = c.execute(select(S.event_log.c.event_id).where((S.event_log.c.patient_id == pid) & (S.event_log.c.kind == "accel")).order_by(S.event_log.c.event_id.desc())).scalar()
            S.insert_ignore(c, S.gt_fall, event_id=eid, patient_id=pid, day=str(ts.date()), ts=ts.isoformat(), subject=a.get("subject"), file=a.get("file"))
        return {"event_id": eid, "day": str(ts.date()), "ts": ts.isoformat(), **r}

    def handle(self, cmd: str, a: dict):
        if cmd == "emu_load":
            return {"patients": self.load(a.get("patients"), a.get("start_day"), a.get("n_days"), a.get("n_patients"))}
        if cmd == "emu_play":
            if not self.events:
                self.load()
            if self.status == "finished":
                raise ValueError("emulation finished: reset first")
            self.status = "playing"
        elif cmd == "emu_pause":
            self.status = "paused" if self.status == "playing" else self.status
        elif cmd == "emu_speed":
            v = float(a["speed"])
            if not 60 <= v <= 86400:
                raise ValueError("speed must be between 60 and 86400 simulated seconds per second")
            self.speed = v
        elif cmd == "emu_inject_fall":
            return self.inject_fall(a)
        else:
            raise ValueError(f"unknown emulator command {cmd}")
        _state(self.eng, status=self.status, speed=self.speed)
        return None

    def process_commands(self) -> int:
        with self.eng.begin() as c:
            rows = c.execute(select(S.sim_command).where((S.sim_command.c.status == "NEW") & S.sim_command.c.cmd.like("emu_%")).order_by(S.sim_command.c.id)).fetchall()
        for r in rows:
            try:
                res = self.handle(r.cmd, json.loads(r.args or "{}"))
                st, out = "DONE", S.j(res)
            except Exception as ex:  # noqa: BLE001 - reported back to the API caller
                st, out = "ERROR", str(ex)[:300]
            with self.eng.begin() as c:
                c.execute(S.sim_command.update().where(S.sim_command.c.id == r.id).values(status=st, result=out))
        return len(rows)

    def run(self, poll_s: float = 0.2):
        while not self.stop.wait(poll_s):
            try:
                self.process_commands()
                self.tick()
            except Exception:  # noqa: BLE001
                log.exception("emulator tick failed")
