"""Replay TIHM day by day into event_log and inject SisFall fall/ADL windows from HELD-OUT subjects.

SYNTHETIC / SIMULATED data: for systems tests and demos only, never a training source (see echos/data/guard.py).
Systems test only: agents 1-2 were final-fitted on all TIHM patients, so replayed predictions are not accuracy
evidence (see reports/metrics for the out-of-fold numbers). The fall model used here excludes the held-out subjects.
"""
from __future__ import annotations

import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from echos.agents import fall
from echos.config import ROOT, load_config
from echos.data import sisfall as sf
from echos.data.guard import assert_real_source
from echos.data.patient_day import load_patient_day
from echos.orchestrator import state as S

REPLAY_MODEL = ROOT / "models" / "fall_replay_v2.joblib"   # v1: models/fall_replay.joblib (kept)


def train_replay_fall_model(force: bool = False) -> Path:
    if REPLAY_MODEL.exists() and not force:
        return REPLAY_MODEL
    cfg = load_config()
    df = fall.build_windows_v2()
    assert_real_source(df, "sisfall")
    tr = df[~df.subject.isin(cfg["replay"]["holdout_subjects"])]
    m = fall._rf(cfg["seed"]).fit(tr[fall.FEATURES], tr.y)
    joblib.dump({"model": m, "features": fall.FEATURES, "threshold": cfg["agent3"]["operating"]["threshold"], "windowing": "v2",
                 "excluded_subjects": cfg["replay"]["holdout_subjects"]}, REPLAY_MODEL)
    return REPLAY_MODEL


def pick_patients(pdy: pd.DataFrame, n: int) -> list:
    ok = pdy[pdy.has_activity].groupby("patient_id").has_reading.sum().sort_values(ascending=False, kind="stable")
    return sorted(ok.index[:n].tolist())


def build_events(n_patients: int = 5, seed: int | None = None, max_days: int | None = None) -> tuple[list, list]:
    """Return (events, ground_truth). Events are in chronological order; ids are assigned on insert."""
    cfg = load_config()
    seed = cfg["seed"] if seed is None else seed
    rc, a3 = cfg["replay"], cfg["agent3"]
    rng = np.random.RandomState(seed)
    pdy = load_patient_day()
    pats = pick_patients(pdy, n_patients)
    files = sf.list_files()
    files = files[files.subject.isin(rc["holdout_subjects"])]
    falls, adls = files[files.is_fall].reset_index(drop=True), files[~files.is_fall].reset_index(drop=True)
    n = int(a3["window_s"] * a3["fs"])
    length_cache: dict = {}

    def n_samples(path):
        if path not in length_cache:
            length_cache[path] = len(sf.load_file(path))
        return length_cache[path]

    events, gt = [], []
    days_by_p = {p: sorted(pdy[pdy.patient_id == p].day.dt.normalize().unique())[:max_days] for p in pats}
    fall_days = {p: set(rng.choice(len(d), min(rc["falls_per_patient"], len(d)), replace=False)) for p, d in days_by_p.items()}
    all_days = sorted({d for ds in days_by_p.values() for d in ds})
    for day in all_days:
        dstr = pd.Timestamp(day).strftime("%Y-%m-%d")
        for p in pats:
            if day not in days_by_p[p]:
                continue
            i = days_by_p[p].index(day)
            day_ev = []
            wins = []
            if i in fall_days[p]:
                r = falls.iloc[rng.randint(len(falls))]
                a = sf.load_file(r.path)
                wins.append((r, int(np.argmax(sf.svm(a))) - n // 2, True))
            for _ in range(rc["adl_windows_per_day"]):
                r = adls.iloc[rng.randint(len(adls))]
                wins.append((r, int(rng.randint(0, n_samples(r.path) - n)), False))
            for r, s, is_fall in wins:
                sec = int(rng.randint(0, 86399))
                ts = (pd.Timestamp(day) + pd.Timedelta(seconds=sec)).isoformat()
                day_ev.append({"patient_id": p, "day": dstr, "ts": ts, "kind": "sensor",
                               "payload": {"file": r.path, "win_start": s, "subject": r.subject, "activity": r.activity},
                               "_fall": is_fall})
            day_ev.sort(key=lambda e: e["ts"])
            day_ev.append({"patient_id": p, "day": dstr, "ts": (pd.Timestamp(day) + pd.Timedelta(seconds=86399)).isoformat(),
                           "kind": "day", "payload": {}, "_fall": False})
            events.extend(day_ev)
    return events, [e for e in events if e["_fall"]]


def replay(engine, n_patients: int = 5, seed: int | None = None, rate_eps: float = 0.0, max_days: int | None = None, on_insert=None) -> dict:
    """Write events to event_log (one transaction per event so consumers see a live stream). rate_eps=0 -> as fast as possible."""
    events, _ = build_events(n_patients, seed, max_days)
    n_fall = 0
    for e in events:
        with engine.begin() as c:
            r = c.execute(S.event_log.insert().values(patient_id=e["patient_id"], day=e["day"], ts=e["ts"], kind=e["kind"],
                                                     payload=S.j(e["payload"])))
            eid = r.inserted_primary_key[0]
            if e["_fall"]:
                n_fall += 1
                S.insert_ignore(c, S.gt_fall, event_id=eid, patient_id=e["patient_id"], day=e["day"], ts=e["ts"],
                                subject=e["payload"]["subject"], file=e["payload"]["file"])
        if on_insert:
            on_insert(eid, e, time.time())     # wall-clock time of the insert (latency measurements)
        if rate_eps > 0:
            time.sleep(1.0 / rate_eps)
    return {"events": len(events), "falls": n_fall}
