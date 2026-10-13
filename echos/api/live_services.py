"""API helpers for live / emulator mode: ingestion, status with per-stream freshness, emulator commands. DB reads and writes only."""
from __future__ import annotations

import json
import os
import time
from typing import Optional

from sqlalchemy import select

from echos.api import services as V
from echos.api.services import Ctx
from echos.live import store as LS
from echos.live.schemas import IngestBatch
from echos.orchestrator import state as S

LABELS = {
    "replay": "REPLAY: the TIHM study replayed day by day for evaluation and demos. Not live data.",
    "emulator": "EMULATOR: real TIHM raw events replayed in wall-clock time by a labelled device emulator. Not a real device; falls are injected from SisFall.",
    "live": "LIVE: events arrive from real devices through the ingestion API.",
}


def mode(ctx: Ctx) -> str:
    with ctx.eng.begin() as c:
        v = c.execute(select(S.sim_state.c.value).where(S.sim_state.c.key == "mode")).scalar()
    return json.loads(v) if v else "replay"


def check_token(token: Optional[str]) -> None:
    want = os.environ.get("ECHOS_INGEST_TOKEN")
    if want and token != want:
        raise PermissionError("missing or wrong X-Echos-Token")


def ingest(ctx: Ctx, batch: IngestBatch) -> dict:
    r = LS.write_events(ctx.eng, batch.events)
    return {**r, "rejected": 0}


def _emu(ctx: Ctx) -> Optional[dict]:
    with ctx.eng.begin() as c:
        kv = {r.key[4:]: json.loads(r.value) for r in c.execute(select(S.sim_state).where(S.sim_state.c.key.like("emu_%")))}
    if not kv:
        return None
    return {"status": kv.get("status", "idle"), "speed": float(kv.get("speed", 0)), "sim_now": kv.get("sim_now"), "cursor": int(kv.get("cursor", 0)), "total": int(kv.get("total", 0)),
            "start": kv.get("start"), "end": kv.get("end"), "patients": kv.get("patients", [])}


def status(ctx: Ctx) -> dict:
    m, now = mode(ctx), time.time()
    emu = _emu(ctx)
    expected = m == "live" or bool(emu and emu["status"] == "playing")
    off = float(ctx.cfg.get("live", {}).get("offline_after_s", 120))
    with ctx.eng.begin() as c:
        lp = c.execute(select(S.live_patient)).fetchall()
        cs = {}
        for r in c.execute(select(S.case_state.c.patient_id, S.case_state.c.day, S.case_state.c.status, S.case_state.c.as_of, S.case_state.c.provisional).order_by(S.case_state.c.day)):
            cs[r.patient_id] = r
    out = []
    for p in lp:
        streams = {}
        for k, col in (("activity", p.wall_activity), ("vitals", p.wall_vitals), ("accel", p.wall_accel), ("heartbeat", p.wall_heartbeat)):
            if col is None:
                streams[k] = {"last_wall_age_s": None, "state": "none"}
                continue
            age = max(0.0, now - col)
            sparse = k in ("vitals", "accel")
            streams[k] = {"last_wall_age_s": round(age, 1), "state": "idle" if sparse else ("stale" if (expected and age > off) else "fresh")}
        c = cs.get(p.patient_id)
        out.append({"patient_id": p.patient_id, "last_event_ts": p.last_event_ts, "open_day": c.day if c else None, "status": c.status if c else None,
                    "provisional": bool(c.provisional) if c else True, "as_of": c.as_of if c else None, "streams": streams})
    return {"mode": m, "label": LABELS.get(m, ""), "emulator": emu, "patients": sorted(out, key=lambda x: x["patient_id"]), "now": now}


def emulator_command(ctx: Ctx, body) -> dict:
    a = body.action
    if mode(ctx) != "emulator":
        raise ValueError("this runtime is not in emulator mode (start it with scripts/run_api.sh --mode live)")
    if a == "fall":
        if not body.patient_id:
            raise ValueError("fall needs patient_id")
        w = V.pick_fall_window(ctx, body.subject, body.activity)
        cid, st, res = V.send_command(ctx, "emu_inject_fall", {"patient_id": body.patient_id, **w}, wait_s=5.0)
        note = f"SYNTHETIC: a SisFall recording ({w['subject']}, {w['activity']}) injected as one accelerometer event at the patient's current data time."
    elif a in ("load", "play", "pause", "speed"):
        args = {k: v for k, v in {"speed": body.speed, "start_day": body.start_day, "n_days": body.n_days, "n_patients": body.n_patients}.items() if v is not None}
        cid, st, res = V.send_command(ctx, f"emu_{a}", args, wait_s=15.0 if a in ("load", "play") else 5.0)
        note = ""
    else:
        raise ValueError("action must be load, play, pause, speed or fall")
    if st == "ERROR":
        raise ValueError(res or "command failed")
    return {"status": "DONE" if st == "DONE" else "QUEUED", "result": json.loads(res) if st == "DONE" and res else None,
            "note": note + ("" if st == "DONE" else " The runtime has not acknowledged the command yet (is it running?).")}
