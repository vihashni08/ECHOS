"""Review Coordinator replay: which allocation policy makes the best use of K clinician reviews per day? -> reports/metrics/v2_allocation.json

Uses the OUT-OF-FOLD scores written by vitals_eval.py and agitation_eval.py (data/v2/*.parquet), so every score a policy sees came from a
model that never trained on that patient. Each day t (end of day, after the 18:00 window) a policy picks K patients for review tomorrow.
  - an agitation episode in a window ending on day t is FOLLOWED UP if the patient is on day t's list;
  - a validated BP / pulse event on day e is ANTICIPATED if the patient was on the list of day e-3, e-2 or e-1.
Policies only see information available at the end of day t (the oracle sees the future, as an upper bound).

    PYTHONPATH=. python scripts/evaluation/allocation_eval.py
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from echos.config import ROOT, load_config
from echos.monitor import allocation as AL

OUT = ROOT / "reports" / "metrics" / "v2_allocation.json"
V2 = ROOT / "data" / "v2"


def load():
    vit = pd.read_parquet(V2 / "oof_vitals.parquet")
    ag = pd.read_parquet(V2 / "oof_agitation.parquet")
    ag["day"] = (ag["w"] - pd.Timedelta(hours=1)).dt.normalize()   # night window ending 08:00 belongs to that morning's day
    agd = ag.groupby(["patient_id", "day"]).agg(a=("p_agitation", "max"), ep=("y", "sum")).reset_index()
    vit = vit.rename(columns={"beta_forecast": "v"})[["patient_id", "day", "v", "event", "sys", "dia", "hr"]]
    d = agd.merge(vit, on=["patient_id", "day"], how="outer")
    d["ep"] = d["ep"].fillna(0).astype(int)
    d["event"] = d["event"].fillna(0).astype(int)
    return d.sort_values(["day", "patient_id"]).reset_index(drop=True)


def run(d: pd.DataFrame, policy: str, k: int, fairness: int | None = None, seed: int = 0) -> dict:
    rng = np.random.default_rng(seed)
    v_bar, a_bar = float(d["v"].mean()), float(d["a"].mean())   # population base rates (no patient-level information)
    by_day = {day: g for day, g in d.groupby("day")}
    days = sorted(by_day)
    ev = {(r.patient_id, r.day) for r in d[d.event == 1].itertuples()}
    ep_total = int(d["ep"].sum())
    last_rev: dict = {}
    first_seen: dict = {}
    lists: dict = {}
    gaps: list = []
    ep_followed = 0
    n_rev = 0
    for t in days:
        g = by_day[t]
        for pid in g.patient_id:
            first_seen.setdefault(pid, t)
        since = {pid: (t - last_rev.get(pid, first_seen[pid])).days for pid in g.patient_id}
        score = AL.prob_union if policy == "prob_union" else (lambda v, a: AL.priority(v, a, v_bar, a_bar))
        cands = [AL.Candidate(r.patient_id, score(r.v, r.a), since[r.patient_id], r.v, r.a) for r in g.itertuples()]
        if policy == "random":
            pick = list(rng.choice([c.patient_id for c in cands], min(k, len(cands)), replace=False))
        elif policy == "round_robin":
            pick = [c.patient_id for c in sorted(cands, key=lambda c: (-c.days_since_review, c.patient_id))[:k]]
        elif policy == "vitals_only":
            pick = [c.patient_id for c in sorted(cands, key=lambda c: (-(0 if c.vitals_risk is None or np.isnan(c.vitals_risk) else c.vitals_risk), c.patient_id))[:k]]
        elif policy == "agitation_only":
            pick = [c.patient_id for c in sorted(cands, key=lambda c: (-(0 if c.agitation_risk is None or np.isnan(c.agitation_risk) else c.agitation_risk), c.patient_id))[:k]]
        elif policy in ("tihm_limits", "bp_160_100"):
            # the simple rules a team could follow without any model: review whoever is past the limit today (highest systolic first)
            from echos.monitor import cohort
            flag = (g.apply(lambda r: bool(cohort.safety_flags({"sys": r.sys, "dia": r.dia, "hr": r.hr})), axis=1) if policy == "tihm_limits"
                    else ((g.sys >= 160) | (g.dia >= 100))).astype(int)
            key = dict(zip(g.patient_id, flag * 1000 + g.sys.fillna(0)))
            pick = sorted(key, key=lambda p: (-key[p], p))[:k]
        elif policy in ("echos", "prob_union"):
            pick = [c.patient_id for c, _ in AL.select(cands, k, fairness)]
        elif policy == "oracle":
            fut = {pid: (by_day[t].set_index("patient_id").ep.get(pid, 0) > 0) * 1.0
                   + sum(((pid, t + pd.Timedelta(days=h)) in ev) for h in (1, 2, 3)) for pid in g.patient_id}
            pick = sorted(fut, key=lambda p: (-fut[p], p))[:k]
        else:
            raise ValueError(policy)
        lists[t] = set(pick)
        n_rev += len(pick)
        eps = g.set_index("patient_id")["ep"]
        ep_followed += int(sum(eps.get(p, 0) for p in pick))
        for p in pick:
            gaps.append(since[p])
            last_rev[p] = t
    anticipated = sum(any(pid in lists.get(e - pd.Timedelta(days=h), set()) for h in (1, 2, 3)) for pid, e in ev)
    never = len(set(first_seen) - set(last_rev))
    end_gap = [(days[-1] - last_rev.get(p, first_seen[p])).days for p in first_seen]
    return {"reviews": n_rev, "agitation_followed_up": ep_followed / max(ep_total, 1), "agitation_episodes": ep_total,
            "vitals_anticipated": anticipated / max(len(ev), 1), "vitals_events": len(ev),
            "events_per_review": (ep_followed + anticipated) / max(n_rev, 1),
            "max_days_without_review": int(max(gaps + end_gap)), "patients_never_reviewed": int(never), "patients": len(first_seen)}


def main() -> dict:
    d = load()
    out = {"priority": "r = max(v / v_bar, a / a_bar)", "setup": {"patients": int(d.patient_id.nunique()), "days": int(d.day.nunique()),
                     "median_active_patients_per_day": float(d.groupby("day").size().median()),
                     "agitation_episodes": int(d.ep.sum()), "vitals_events": int(d.event.sum()),
                     "definition": "agitation followed up = reviewed the same day; vitals event anticipated = reviewed 1-3 days before"},
           "by_k": {}}
    policies = [("random", None), ("round_robin", None), ("vitals_only", None), ("agitation_only", None), ("prob_union", None), ("tihm_limits", None), ("bp_160_100", None), ("echos", None), ("echos", 7),
                ("oracle", None)]
    for k in (2, 4, 6):
        res = {}
        for pol, fair in policies:
            name = pol + (f"+fairness{fair}" if fair else "")
            if pol == "random":
                rs = [run(d, pol, k, fair, seed=s) for s in range(5)]
                res[name] = {key: float(np.mean([r[key] for r in rs])) for key in rs[0]}
            else:
                res[name] = run(d, pol, k, fair)
            r = res[name]
            print(f"K={k} {name:20s} agitation followed {r['agitation_followed_up']:.2f}  vitals anticipated {r['vitals_anticipated']:.2f}  "
                  f"events/review {r['events_per_review']:.3f}  max gap {r['max_days_without_review']:.0f}d  never {r['patients_never_reviewed']:.0f}")
        out["by_k"][str(k)] = res
    OUT.write_text(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    main()
