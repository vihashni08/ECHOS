"""Does the Case Investigator's computed evidence mean anything? Offline, no LLM / PubMed / e-mail.

Population: TIHM reading-days where the person is outside their own usual on a vital (|robust z| >= 2 against their PAST readings), i.e. the days the
Investigator would be asked about. Outcome: a recorded clinical event on that day (blood pressure / pulse / body-water label). Signals (all computed
from data before that day only, via echos/team/evidence_tools.py):
  trend      - the reading is on a trend away from the usual (check_trend.confirmed)
  corrob     - night-time or total activity is also outside the usual (check_corroboration.agrees)
  history    - >= 50% of this person's earlier deviation days carried an event (needs >= 2 earlier deviation days)
  never      - >= 3 earlier deviation days and none carried an event (the 'weakens' signal)
  net        - supports minus weakens, as the Investigator uses it to move the evidence level
Reports event rate with / without each signal, the difference, and a patient-level bootstrap 95% CI (resampling patients, 1000x).
Honest reading: ~50 patients, labels of unverified provenance; a CI that includes 0 means NOT shown to separate. Not clinical validation.

Part 2 (`level_rules`): the Investigator's base evidence-level rules on the replayed non-Normal days (reports/metrics/e2e_days.json, patient-level
out-of-fold scores, 20 patients): agreement (vitals AND agitation flagged), repeat (the previous day was also non-Normal), persistent vitals (2 of the
last 3 readings beyond |z| 2), caveat (a data gap), and the resulting level STRONG / MODERATE / LIMITED. Outcome: any recorded clinical event that day
(blood pressure / pulse / body water / agitation). Falls are excluded (TIHM has no fall labels; injected falls are synthetic).
Usage: PYTHONPATH=. python scripts/investigator_eval.py  -> reports/metrics/investigator_eval.json"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from echos.data.patient_day import load_patient_day          # noqa: E402
from echos.team import evidence_tools as ET                    # noqa: E402

SEED, N_BOOT = 42, 1000


def build() -> pd.DataFrame:
    pdy = load_patient_day()
    rows = []
    for pid, g in pdy.groupby("patient_id"):
        g = g.sort_values("day")
        rd = g[g.has_reading].reset_index(drop=True)
        for i in range(len(rd)):
            row = rd.iloc[i]
            zs = {v: ET.robust_z(rd[v].iloc[:i].tolist(), row[v], v)[0] for v in ET.VITALS}
            zs = {v: z for v, z in zs.items() if z is not None}
            if not zs or max(abs(z) for z in zs.values()) < ET.Z_FLAG:
                continue
            worst = max(zs, key=lambda v: abs(zs[v]))
            tr = ET.trend_check(rd[worst].iloc[:i].tolist(), row[worst], worst)
            day = str(row.day.date())
            hp = ET.personal_history(pdy, pid, day)
            co = ET.corroboration(pdy, pid, day, True, False)
            hist_sup = hp["prior_deviation_days"] >= 2 and hp["prior_deviation_days_with_event"] >= 0.5 * hp["prior_deviation_days"]
            never = hp["prior_deviation_days"] >= 3 and hp["prior_deviation_days_with_event"] == 0
            trend = bool(tr.get("confirmed"))
            corr = bool(co.get("agrees"))
            rows.append({"patient_id": pid, "day": day, "y": int(bool(row.bp_alert or row.pulse_alert or row.water_alert)),
                         "trend": trend, "corrob": corr, "history": bool(hist_sup), "never": bool(never),
                         "net": int(trend) + int(corr) + int(hist_sup) - int(never)})
    return pd.DataFrame(rows)


def rate(d: pd.DataFrame):
    return float(d.y.mean()) if len(d) else float("nan")


def effect(d: pd.DataFrame, flag: pd.Series) -> float:
    a, b = d[flag], d[~flag]
    return rate(a) - rate(b) if len(a) and len(b) else float("nan")


def level_rules() -> dict:
    days = json.loads((ROOT / "reports" / "metrics" / "e2e_days.json").read_text())
    pdy = load_patient_day().copy()
    pdy["day"] = pdy.day.dt.strftime("%Y-%m-%d")
    lab = pdy.set_index(["patient_id", "day"])
    status = {(x["patient_id"], x["day"]): x["status"] for x in days}
    rows = []
    for x in days:
        if x["status"] == "NORMAL" or x["injected_fall"] or any("fall" in r for r in x["reasons"]):
            continue
        key = (x["patient_id"], x["day"])
        row = lab.loc[key] if key in lab.index else None
        y = int(row is not None and bool(row.bp_alert or row.pulse_alert or row.water_alert or row.agitation))
        reasons = " ".join(x["reasons"])
        vit, beh = "vitals alert" in reasons, "agitation" in reasons
        absolute = "critical vitals" in reasons
        prev = (pd.Timestamp(x["day"]) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        repeat = status.get((x["patient_id"], prev), "NORMAL") != "NORMAL"
        g = pdy[(pdy.patient_id == x["patient_id"]) & pdy.has_reading & (pdy.day <= x["day"])].sort_values("day")
        persistent = False
        if vit and len(g) >= 4:
            zs = []
            for v in ET.VITALS:
                col = g[v].tolist()
                zs.append([ET.robust_z(col[:i], col[i], v)[0] for i in range(len(col) - 3, len(col))])
            persistent = any(sum(1 for z in zz if z is not None and abs(z) >= ET.Z_FLAG) >= 2 for zz in zs)
        caveat = bool(x["data_gap"])
        n_src = int(vit) + int(beh)
        level = "STRONG" if absolute or n_src >= 2 else "MODERATE" if (persistent or repeat) and not caveat and n_src else "LIMITED"
        rows.append({"patient_id": x["patient_id"], "y": y, "agree": n_src >= 2, "repeat": repeat, "persistent": persistent, "caveat": caveat,
                     "flagged": n_src >= 1, "level": level})
    d = pd.DataFrame(rows)
    pats = d.patient_id.unique()
    by = {p: g for p, g in d.groupby("patient_id")}
    rng = np.random.default_rng(SEED)
    out = {"n_days": int(len(d)), "n_patients": int(len(pats)), "event_rate_all": rate(d), "by_level": {}, "signals": {}}

    def boot(fn):
        v = []
        for _ in range(N_BOOT):
            s = pd.concat([by[p] for p in rng.choice(pats, len(pats), replace=True)])
            r = fn(s)
            if r == r:
                v.append(r)
        return [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))] if v else [None, None]
    for lv in ("STRONG", "MODERATE", "LIMITED"):
        out["by_level"][lv] = {"n": int((d.level == lv).sum()), "event_rate": rate(d[d.level == lv])}
    for a, b in (("STRONG", "LIMITED"), ("MODERATE", "LIMITED"), ("STRONG", "MODERATE")):
        diff = rate(d[d.level == a]) - rate(d[d.level == b])
        ci = boot(lambda s: rate(s[s.level == a]) - rate(s[s.level == b]))
        out["by_level"][f"{a} minus {b}"] = {"difference": diff, "ci95": ci, "separates": bool(ci[0] is not None and (ci[0] > 0 or ci[1] < 0))}
    for name in ("agree", "repeat", "persistent", "caveat"):
        sub = d[d.flagged] if name != "caveat" else d
        diff = effect(sub, sub[name])
        ci = boot(lambda s, n=name: effect(s[s.flagged] if n != "caveat" else s, (s[s.flagged] if n != "caveat" else s)[n]))
        out["signals"][name] = {"n_with": int(sub[name].sum()), "n_without": int((~sub[name]).sum()), "event_rate_with": rate(sub[sub[name]]),
                                "event_rate_without": rate(sub[~sub[name]]), "difference": diff, "ci95": ci,
                                "separates": bool(ci[0] is not None and (ci[0] > 0 or ci[1] < 0))}
    return out


def main() -> int:
    d = build()
    pats = d.patient_id.unique()
    by_pat = {p: g for p, g in d.groupby("patient_id")}
    rng = np.random.default_rng(SEED)
    out = {"n_days": int(len(d)), "n_patients": int(len(pats)), "event_rate_all_deviation_days": rate(d), "signals": {},
           "notes": ["population: reading-days outside the person's own usual on a vital (|z|>=2, past-only); outcome: same-day BP / pulse / body-water label",
                     "all signals use data before the day only; difference = event rate with the signal minus without it; CI = patient bootstrap (1000)",
                     "a CI that includes 0 means the signal is NOT shown to separate event days from non-event days in this cohort",
                     "labels are described as clinician-verified but we did not verify how they were produced: associative, not clinical validation"]}
    signals = {"trend": d.trend, "corrob": d.corrob, "history": d.history, "never (weakens)": d.never, "net >= 1": d.net >= 1, "net <= -1": d.net <= -1}
    for name, flag in signals.items():
        diffs = []
        for _ in range(N_BOOT):
            s = pd.concat([by_pat[p] for p in rng.choice(pats, len(pats), replace=True)])
            f = (s.net >= 1) if name == "net >= 1" else (s.net <= -1) if name == "net <= -1" else s[name.split(" ")[0]]
            v = effect(s, f)
            if not np.isnan(v):
                diffs.append(v)
        lo, hi = (float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))) if diffs else (None, None)
        out["signals"][name] = {"n_with": int(flag.sum()), "n_without": int((~flag).sum()), "event_rate_with": rate(d[flag]),
                                "event_rate_without": rate(d[~flag]), "difference": effect(d, flag), "ci95": [lo, hi],
                                "separates": bool(lo is not None and (lo > 0 or hi < 0))}
    out["level_rules"] = lr = level_rules()
    print(f"level rules: {lr['n_days']} non-Normal non-fall days ({lr['n_patients']} patients), event rate {lr['event_rate_all']:.3f}")
    for k, v in lr["by_level"].items():
        print("  ", k, {a: (round(b, 3) if isinstance(b, float) else b) for a, b in v.items()})
    for k, v in lr["signals"].items():
        print(f"   {k:11s} with n={v['n_with']:3d} {v['event_rate_with']:.3f} | without n={v['n_without']:3d} {v['event_rate_without']:.3f} | diff {v['difference']:+.3f} "
              f"CI [{v['ci95'][0]:+.3f}, {v['ci95'][1]:+.3f}] {'SEPARATES' if v['separates'] else 'not shown'}")
    p = ROOT / "reports" / "metrics" / "investigator_eval.json"
    p.write_text(json.dumps(out, indent=1))
    print(f"deviation days {out['n_days']} ({out['n_patients']} patients), event rate {out['event_rate_all_deviation_days']:.3f}")
    for k, v in out["signals"].items():
        print(f"  {k:16s} with n={v['n_with']:4d} rate={v['event_rate_with']:.3f} | without n={v['n_without']:4d} rate={v['event_rate_without']:.3f} | "
              f"diff {v['difference']:+.3f} CI [{v['ci95'][0]:+.3f}, {v['ci95'][1]:+.3f}] {'SEPARATES' if v['separates'] else 'not shown'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
