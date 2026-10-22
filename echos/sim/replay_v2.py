"""Replay real TIHM data day by day for the v2 agents (no synthetic data).

For every simulated day, in this order:
  'day'       per active patient: that day's vitals (daily max BP / HR, mean temperature / weight / body water, time of the first reading) and
              the activity-sensor windows ending that day (night 18-08, 08-12, 12-18) as window features
  'outcome'   per active patient: what TIHM's monitoring team confirmed that day (BP / pulse event, agitation windows). The Care agent turns it
              into FEEDBACK for the Patient Monitor, so labels reach a monitor only AFTER it scored the day.
  'day_close' one per day: who was active, so the Review Coordinator knows when every request is in.
Each patient is scored by models from the cross-validation fold that excluded them (echos/monitor/store.py), so replay scores are out-of-fold."""
from __future__ import annotations

import math
import os

import pandas as pd

from echos.data import loaders
from echos.monitor import agitation as A, cohort


def _clean(d: dict) -> dict:
    return {k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in d.items()}


def pick_window(vit: pd.DataFrame, wins: pd.DataFrame, labels: pd.DataFrame, n: int, max_days: int) -> tuple[list, pd.Timestamp]:
    """Demo cohort: the start day (weekly steps) and n patients with vitals AND activity, active on >= half of the days, that show the most
    confirmed events in the window (a demo should show the agents at work). Selection only - it never touches the models."""
    both = sorted(set(vit.patient_id) & set(wins.patient_id))
    act_days = pd.concat([vit[vit.has_reading == 1][["patient_id", "day"]], wins.assign(day=(wins.w - pd.Timedelta(hours=1)).dt.normalize())[["patient_id", "day"]]])
    act_days = act_days[act_days.patient_id.isin(both)].drop_duplicates()
    lab = labels[labels.patient_id.isin(both) & labels.type.isin(["Blood pressure", "Pulse", "Agitation"])][["patient_id", "day"]]
    best = (-1, [], None)
    for start in pd.date_range(act_days.day.min(), act_days.day.max() - pd.Timedelta(days=max_days - 1), freq="7D"):
        end = start + pd.Timedelta(days=max_days)
        a = act_days[(act_days.day >= start) & (act_days.day < end)].groupby("patient_id").size()
        ok = a[a >= max_days / 2].index
        e = lab[(lab.day >= start) & (lab.day < end) & lab.patient_id.isin(ok)].groupby("patient_id").size()
        top = sorted(ok, key=lambda p: (-int(e.get(p, 0)), p))[:n]
        score = int(sum(e.get(p, 0) for p in top))
        if score > best[0]:
            best = (score, top, start)
    return best[1], best[2]


def build_events(n_patients: int = 8, max_days: int = 28, start: str | None = None, patients: list | None = None) -> list:
    phys, labels, act = loaders.load_physiology(), loaders.load_labels(), loaders.load_activity()
    vit = cohort.vitals_cohort(phys, labels)
    env_p = [x for x in os.environ.get("ECHOS_DEMO_PATIENTS", "").split(",") if x]
    wins_all = A.window_features(act[act.patient_id.isin(set(vit.patient_id))])
    start = start or os.environ.get("ECHOS_DEMO_START")
    if patients or env_p:
        pats = sorted(patients or env_p)
    else:
        pats, auto_start = pick_window(vit, wins_all, labels, n_patients, max_days)
        pats, start = sorted(pats), start or auto_start
    dv = cohort.daily_vitals(phys[phys.patient_id.isin(pats)])
    first_read = phys[phys.patient_id.isin(pats) & phys.device_type.isin(cohort.VITALS)].groupby(["patient_id", "day"]).date.min()
    wins = wins_all[wins_all.patient_id.isin(pats)].copy()
    wins["day"] = (wins["w"] - pd.Timedelta(hours=1)).dt.normalize()
    ag = labels[(labels.type == "Agitation") & labels.patient_id.isin(pats)].copy()
    ag["w"] = A.label_window(pd.to_datetime(ag["date"]))
    ag_set = {(r.patient_id, r.w) for r in ag.itertuples()}
    ev = {(r.patient_id, r.day) for r in labels[labels.type.isin(cohort.VITAL_LABELS) & labels.patient_id.isin(pats)].itertuples()}
    days = sorted(set(dv.day) | set(wins.day))
    if start:
        days = [d for d in days if d >= pd.Timestamp(start)]
    days = days[:max_days]
    vit_by = {(r["patient_id"], r["day"]): r for r in dv.to_dict("records")}
    win_by: dict = {}
    for r in wins.to_dict("records"):
        win_by.setdefault((r["patient_id"], r["day"]), []).append(r)
    events = []
    for day in days:
        ds = day.strftime("%Y-%m-%d")
        active = [p for p in pats if (p, day) in vit_by or (p, day) in win_by]
        for p in active:
            v = vit_by.get((p, day), {})
            readings = _clean({k: v.get(k) for k in ("sys", "dia", "hr", "temp", "weight", "water")})
            t = first_read.get((p, day))
            ws = [{"w": r["w"].isoformat(), "slot": int(r["slot"]), **{c: float(r[c]) for c in A.RAW}} for r in sorted(win_by.get((p, day), []), key=lambda r: r["w"])]
            ts = (t.isoformat() if t is not None and not pd.isna(t) else f"{ds}T18:00:00")
            events.append({"patient_id": p, "day": ds, "ts": f"{ds}T18:00:00", "kind": "day",
                           "payload": {"vitals": readings, "vitals_time": ts, "windows": ws}})
        for p in active:
            aw = [w["w"] for w in [{"w": r["w"].isoformat(), "raw": r["w"]} for r in win_by.get((p, day), [])] if (p, pd.Timestamp(w["w"])) in ag_set]
            events.append({"patient_id": p, "day": ds, "ts": f"{ds}T20:00:00", "kind": "outcome",
                           "payload": {"vitals_event": (p, day) in ev, "agitation_windows": aw}})
        events.append({"patient_id": "", "day": ds, "ts": f"{ds}T23:59:59", "kind": "day_close", "payload": {"day": ds, "patients": active}})
    return events
