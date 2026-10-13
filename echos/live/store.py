"""Live event store: writes ingested events to event_log (idempotent), tracks per-stream freshness, reads a day's events back as frames."""
from __future__ import annotations

import json
import time
from typing import Iterable

import pandas as pd
from sqlalchemy import select

from echos.live.schemas import IngestEvent
from echos.orchestrator import state as S

LIVE_KINDS = ("activity", "vitals", "accel", "heartbeat")


def _payload(e: IngestEvent) -> dict:
    d = {"device_id": e.device_id, "source": e.source}
    if e.kind == "activity":
        d["room"] = e.room
    elif e.kind == "vitals":
        d.update(device_type=e.device_type, value=e.value, unit=e.unit)
    elif e.kind == "accel":
        d.update(fs=e.fs, samples=e.samples)
    return d


def write_events(eng, events: Iterable[IngestEvent]) -> dict:
    """Idempotent by (patient, device, kind, ts, room/type): an exact repeat is a duplicate, never a second row."""
    acc = dup = 0
    patients = set()
    now = time.time()
    with eng.begin() as c:
        for e in events:
            ts = e.ts.replace(tzinfo=None).isoformat(timespec="seconds") if e.ts.tzinfo else e.ts.isoformat(timespec="seconds")
            key = f"{e.device_id}|{e.kind}|{ts}|{e.room or e.device_type or ''}"
            exists = c.execute(select(S.event_log.c.event_id).where((S.event_log.c.patient_id == e.patient_id) & (S.event_log.c.kind == e.kind)
                                                                    & (S.event_log.c.ts == ts) & (S.event_log.c.payload.like(f'%"device_id": "{e.device_id}"%'))
                                                                    & ((S.event_log.c.payload.like(f'%"room": "{e.room}"%')) if e.room else (S.event_log.c.payload.like(f'%"device_type": "{e.device_type}"%') if e.device_type else (S.event_log.c.kind == e.kind))))).first()
            if exists:
                dup += 1
                continue
            c.execute(S.event_log.insert().values(patient_id=e.patient_id, day=ts[:10], ts=ts, kind=e.kind, payload=json.dumps(_payload(e))))
            acc += 1
            patients.add(e.patient_id)
            col = {"activity": "wall_activity", "vitals": "wall_vitals", "accel": "wall_accel", "heartbeat": "wall_heartbeat"}[e.kind]
            row = c.execute(select(S.live_patient).where(S.live_patient.c.patient_id == e.patient_id)).first()
            vals = {col: now}
            if row is None:
                S.upsert(c, S.live_patient, ["patient_id"], patient_id=e.patient_id, source=e.source, first_wall=now, last_event_ts=ts, **vals)
            else:
                S.upsert(c, S.live_patient, ["patient_id"], patient_id=e.patient_id, source=row.source or e.source, last_event_ts=max(row.last_event_ts or "", ts), **vals)
    return {"accepted": acc, "duplicates": dup, "patients": sorted(patients)}


def events_of_day(eng, patient_id: str, day: str) -> list:
    with eng.begin() as c:
        return c.execute(select(S.event_log).where((S.event_log.c.patient_id == patient_id) & (S.event_log.c.day == day)
                                                   & S.event_log.c.kind.in_(LIVE_KINDS)).order_by(S.event_log.c.event_id)).fetchall()


def frames_of_day(rows) -> tuple:
    """-> (activity df [patient_id, location_name, date, day], vitals df [patient_id, device_type, value, date, day])"""
    act, vit = [], []
    for r in rows:
        pl = json.loads(r.payload)
        if r.kind == "activity":
            act.append({"patient_id": r.patient_id, "location_name": pl["room"], "date": pd.Timestamp(r.ts)})
        elif r.kind == "vitals":
            vit.append({"patient_id": r.patient_id, "device_type": pl["device_type"], "value": pl["value"], "date": pd.Timestamp(r.ts)})
    a = pd.DataFrame(act, columns=["patient_id", "location_name", "date"])
    v = pd.DataFrame(vit, columns=["patient_id", "device_type", "value", "date"])
    for f in (a, v):
        f["date"] = pd.to_datetime(f["date"])
        f["day"] = f["date"].dt.normalize()
    return a, v


def closed_rows(eng, patient_id: str) -> pd.DataFrame:
    with eng.begin() as c:
        rows = c.execute(select(S.live_day.c.row).where(S.live_day.c.patient_id == patient_id)).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([json.loads(r[0]) for r in rows])
    df["day"] = pd.to_datetime(df["day"])
    return df.sort_values("day").reset_index(drop=True)


def save_row(eng, patient_id: str, day: str, row: dict, closed: bool) -> None:
    with eng.begin() as c:
        S.upsert(c, S.live_day, ["patient_id", "day"], patient_id=patient_id, day=day, row=json.dumps(row, default=str), closed=closed, updated_ts=time.time())


def live_pdy(eng, base: pd.DataFrame) -> pd.DataFrame:
    """The training table plus every live day row: what the report facts and the UI charts read. A live (patient, day) replaces the same row of the
    training table (the emulator replays TIHM dates)."""
    with eng.begin() as c:
        rows = c.execute(select(S.live_day.c.row)).fetchall()
    if not rows:
        return base
    live = pd.DataFrame([json.loads(r[0]) for r in rows])
    live["day"] = pd.to_datetime(live["day"])
    key = set(zip(live.patient_id, live.day))
    keep = base[[(p, d) not in key for p, d in zip(base.patient_id, base.day)]]
    return pd.concat([keep, live], ignore_index=True, sort=False).sort_values(["patient_id", "day"]).reset_index(drop=True)


def _live_frame(eng, kind: str) -> pd.DataFrame:
    with eng.begin() as c:
        rows = c.execute(select(S.event_log.c.patient_id, S.event_log.c.ts, S.event_log.c.payload).where(S.event_log.c.kind == kind)).fetchall()
    if not rows:
        return pd.DataFrame()
    if kind == "activity":
        df = pd.DataFrame({"patient_id": [r[0] for r in rows], "location_name": [json.loads(r[2])["room"] for r in rows], "date": pd.to_datetime([r[1] for r in rows])})
    else:
        pl = [json.loads(r[2]) for r in rows]
        df = pd.DataFrame({"patient_id": [r[0] for r in rows], "device_type": [p["device_type"] for p in pl], "value": [p["value"] for p in pl],
                           "unit": [p.get("unit") for p in pl], "date": pd.to_datetime([r[1] for r in rows])})
    df["day"] = df["date"].dt.normalize()
    return df


def merge_live(eng, base: pd.DataFrame, kind: str) -> pd.DataFrame:
    """Raw frames (activity / vitals) = the TIHM CSV plus every live event; a live (patient, day) replaces the CSV rows of that day (no double count)."""
    live = _live_frame(eng, "activity" if kind == "activity" else "vitals")
    if live.empty:
        return base
    key = set(zip(live.patient_id, live.day))
    keep = base[[(p, d) not in key for p, d in zip(base.patient_id, base.day)]]
    return pd.concat([keep, live[[c for c in base.columns if c in live.columns]]], ignore_index=True, sort=False)
