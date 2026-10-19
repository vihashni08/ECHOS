"""Fall evaluation on the final pipeline (operating point from config agent3.operating: threshold 0.8 + post-fall inactivity confirmation).
(i)  per-fall-type sensitivity F01-F15, adults / the one elderly faller (SE06) separately, subject-level folds (out-of-fold RF, v2 windows);
(ii) end-to-end detection of injected HELD-OUT-subject windows (from the replay run in reports/metrics/e2e.json, scripts/e2e.py) with Wilson 95% CI,
     false fall alerts per patient-week on non-injected days, and event -> alert-queued / alert-sent latency of falls injected through the live
     controller (clean case of reports/metrics/fault_injection.json: run scripts/fault_injection.py first);
(iii) effect of inactivity confirmation on false alerts (sliding-window ADL false alarms per hour; replay false alerts per patient-week).
Writes reports/metrics/fall_eval.json and the block between <!-- fall_eval:start/end --> in reports/RESULTS.md.
Usage: PYTHONPATH=. python scripts/fall_eval.py   (run scripts/e2e.py first; ~3 min)"""
from __future__ import annotations

import json
import re
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
from echos.offline import force_offline  # noqa: E402

force_offline()
from echos.agents import fall as F  # noqa: E402
from echos.config import ROOT, load_config  # noqa: E402
from echos.data import sisfall as sf  # noqa: E402
from echos.orchestrator import state as S  # noqa: E402
from scripts.e2e import wilson  # noqa: E402

OUT = ROOT / "reports/metrics/fall_eval.json"
RESULTS = ROOT / "reports/RESULTS.md"
START, END = "<!-- fall_eval:start -->", "<!-- fall_eval:end -->"


def descriptions() -> dict:
    txt = (ROOT / "data/sisfall/SisFall_dataset/Readme.txt").read_bytes().decode("latin1")
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"^\|\s*(F\d\d)\s*\|\s*([^|]+?)\s*\|", txt, re.M)}


def per_type(v2: pd.DataFrame, oof: np.ndarray, thr: float) -> list[dict]:
    files = sf.list_files()
    files = files[files.is_fall]
    desc = descriptions()
    fall = v2.assign(oof=oof)[v2.kind == "fall"]
    rows = []
    for act in sorted(files.activity.unique()):
        row = {"type": act, "description": desc.get(act, "")}
        for g in ("adult", "elderly"):
            n_files = int(((files.activity == act) & (files.group == g)).sum())
            d = fall[(fall.activity == act) & (fall.group == g)]
            n = len(d)
            hit_p = int((d.oof >= thr).sum())
            hit = int(((d.oof >= thr) & d.post_inactive).sum())
            row[g] = {"trials": n_files, "windowed": n, "p_ge_thr": hit_p, "detected": hit,
                      "sensitivity": hit / n if n else None, "ci95": list(wilson(hit, n)) if n else None,
                      "sensitivity_without_confirmation": hit_p / n if n else None,
                      "median_score": float(d.oof.median()) if n else None}
        rows.append(row)
    return rows


def live_latency() -> dict:
    """Wall-clock latency of falls INJECTED THROUGH THE LIVE CONTROLLER (same path as POST /api/sim/fall: the patient's day closes at once) in the clean
    fault-injection run (scripts/fault_injection.py clean): injection -> alert queued (outbox row) -> alert sent (FakeMailer)."""
    clean = json.loads((ROOT / "reports/metrics/fault_injection.json").read_text())["cases"]["clean"]
    s = [x for x in clean["injected_alerts_all"] if x["queued_s"] is not None]
    q = lambda x, p: round(float(np.percentile(x, p)), 2) if x else None   # noqa: E731
    qs, ss = [x["queued_s"] for x in s], [x["sent_s"] for x in s if x["sent_s"] is not None]
    return {"patients": 3, "days": 10, "n_injected_falls": len(clean["injected_alerts_all"]), "fall_alerts": len(s),
            "event_to_queued_s": {"median": q(qs, 50), "max": q(qs, 100)}, "event_to_sent_s": {"median": q(ss, 50), "max": q(ss, 100)},
            "note": "wall clock, 3 real worker processes + orchestrator, FakeMailer (no network); small sample. In the replay timeline the orchestrator decides at day close "
                    "(daily batch), see detection_delay_sim_hours"}


def pct(x):
    return "n/a" if x is None else f"{x:.2f}"


def main():
    cfg = load_config()
    op = cfg["agent3"]["operating"]
    thr, seed = op["threshold"], cfg["seed"]
    v2 = F.build_windows_v2().reset_index(drop=True)
    oof = F.score_oof(v2, seed)
    types = per_type(v2, oof, thr)
    conf, noconf = F.metrics_at(v2, oof, thr, True), F.metrics_at(v2, oof, thr, False)
    e2e = json.loads((ROOT / "reports/metrics/e2e.json").read_text())
    lat = live_latency()
    k, n = e2e["falls_detected_as_urgent"], e2e["injected_falls"]
    res = {"operating_point": op, "n_subjects": int(v2.subject.nunique()), "per_type": types,
           "group_with_confirmation": conf["groups"], "group_without_confirmation": noconf["groups"],
           "false_alarms_per_hour_overall": {"with_confirmation": conf["overall_false_alarms_per_hour"], "without_confirmation": noconf["overall_false_alarms_per_hour"]},
           "injected_end_to_end": {"patients": e2e["patients"], "patient_weeks": e2e["patient_weeks"], "injected": n, "detected": k,
                                   "detection_rate": k / max(1, n), "wilson95": list(wilson(k, n)), "missed": e2e["missed_injected_falls"],
                                   "false_fall_alerts": e2e["false_fall_alerts"], "false_fall_alerts_per_patient_week": e2e["false_fall_alerts_per_patient_week"],
                                   "sim_delay_h_median": e2e["detection_delay_sim_hours_median"], "sim_delay_h_max": e2e["detection_delay_sim_hours_max"]},
           "live_latency": lat, "inactivity_effect_replay": e2e["inactivity_confirmation"]}
    OUT.write_text(json.dumps(res, indent=1, default=float))
    md = [START, "## Fall evaluation on the final pipeline (written by scripts/fall_eval.py)", "",
          f"Operating point: score >= {thr} AND post-fall inactivity (config `agent3.operating`). Out-of-fold random forest, v2 windows, subject-level folds "
          f"({res['n_subjects']} subjects). Elderly = SE06 only (the single elderly subject who performed falls): 5 trials per type, so every elderly cell has n = 5.", "",
          "### (i) Sensitivity by fall type", "",
          "| Type | Description | Adults: trials | Adults: detected (sens., Wilson 95% CI) | Adults: score only | Elderly SE06: trials | Elderly: detected (sens.) | Elderly: score only |",
          "|---|---|---|---|---|---|---|---|"]
    for r in types:
        a, e = r["adult"], r["elderly"]
        ci = f"{a['detected']}/{a['windowed']} ({a['sensitivity']:.2f}; {a['ci95'][0]:.2f}-{a['ci95'][1]:.2f})" if a["windowed"] else "n/a"
        el = f"{e['detected']}/{e['windowed']} ({e['sensitivity']:.2f})" if e["windowed"] else "n/a"
        md.append(f"| {r['type']} | {r['description']} | {a['trials']} | {ci} | {pct(a['sensitivity_without_confirmation'])} | {e['trials']} | {el} | {pct(e['sensitivity_without_confirmation'])} |")
    weak = sorted([r for r in types if r["adult"]["sensitivity"] is not None and r["adult"]["sensitivity"] < 0.9], key=lambda r: r["adult"]["sensitivity"])
    weak_e = sorted([r for r in types if r["elderly"]["sensitivity"] is not None and r["elderly"]["sensitivity"] < 0.5], key=lambda r: r["elderly"]["sensitivity"])
    md += ["", "**Missed / weak fall types (adults, sensitivity < 0.90 at the operating point):** "
           + ("; ".join(f"{r['type']} {r['description'].lower()} ({r['adult']['sensitivity']:.2f})" for r in weak) or "none")
           + ". **Elderly SE06 below 0.50:** " + ("; ".join(f"{r['type']} ({r['elderly']['sensitivity']:.2f})" for r in weak_e) or "none") + ".", "",
           "### (ii) End-to-end detection of injected held-out-subject falls (full pipeline replay)", "",
           "| Item | Value |", "|---|---|",
           f"| Injected falls ({e2e['patients']} patients, {e2e['patient_weeks']} patient-weeks; subjects SA03, SA11, SA19, SE06 held out of the model) | {n} |",
           f"| Detected as URGENT fall alert | {k}/{n} = {k / max(1, n):.3f} (Wilson 95% CI {wilson(k, n)[0]:.3f}-{wilson(k, n)[1]:.3f}) |",
           f"| False fall alerts on non-injected days | {e2e['false_fall_alerts']} = {e2e['false_fall_alerts_per_patient_week']} per patient-week |",
           f"| Simulated event -> alert delay (daily batch: decided at day close), median / max | {e2e['detection_delay_sim_hours_median']} h / {e2e['detection_delay_sim_hours_max']} h |",
           f"| Wall clock injected fall (live controller, day closes at once) -> alert queued, median / max ({lat['fall_alerts']} alerts, {lat['patients']} patients x {lat['days']} days) | {lat['event_to_queued_s']['median']} s / {lat['event_to_queued_s']['max']} s |",
           f"| Wall clock injected fall -> alert sent (fake mailer, no network), median / max | {lat['event_to_sent_s']['median']} s / {lat['event_to_sent_s']['max']} s |",
           "", "The wall-clock rows measure software latency only; the clinically relevant delay is the simulated one, because the orchestrator "
           "decides once per patient-day.", ""]
    if e2e["missed_injected_falls"]:
        md += ["Missed injected falls: " + ", ".join(f"{m['trial']} ({m['subject']})" for m in e2e["missed_injected_falls"]) + ".", ""]
    ic = e2e["inactivity_confirmation"]
    g_c, g_n = conf["groups"], noconf["groups"]
    md += ["### (iii) Effect of inactivity confirmation on false alerts", "",
           "| Measure | Score only (threshold %.1f) | Score + inactivity confirmation |" % thr, "|---|---|---|",
           f"| SisFall sliding ADL windows: false alarms / hour (all subjects) | {noconf['overall_false_alarms_per_hour']:.2f} | {conf['overall_false_alarms_per_hour']:.2f} |",
           f"| Adult fall sensitivity (out-of-fold) | {g_n['adult']['sensitivity']:.3f} | {g_c['adult']['sensitivity']:.3f} |",
           f"| Elderly (SE06) fall sensitivity | {g_n['elderly']['sensitivity']:.3f} | {g_c['elderly']['sensitivity']:.3f} |",
           f"| Replay: false fall alert days per patient-week | {ic['false_fall_alerts_per_patient_week_without_confirmation']} ({ic['false_fall_alert_days_without_confirmation']} days) | {ic['false_fall_alerts_per_patient_week_with_confirmation']} ({ic['false_fall_alert_days_with_confirmation']} days) |",
           f"| Replay: injected falls with score >= threshold / alerted | {ic['injected_p_ge_thr']}/{ic['injected_events']} | {ic['injected_urgent_with_confirmation']}/{ic['injected_events']} |",
           "", "Without confirmation a flagged event would be URGENT on score alone; in the shipped rules it is only MONITOR (\"possible fall, no inactivity\").",
           "", "Caveats: SisFall falls are scripted on mats by young volunteers; the elderly column is one subject; ADL windows are short vigorous trials, so alarms per hour "
           "overstate daily life; the replay uses 4 held-out subjects, so its detection rate is a pipeline check, not clinical sensitivity.", END]
    s = RESULTS.read_text()
    block = "\n".join(md)
    if START in s:
        s = s[:s.index(START)] + block + s[s.index(END) + len(END):]
    else:
        s = s.rstrip() + "\n\n" + block + "\n"
    RESULTS.write_text(s)
    print("| type | adults det/n | adults score-only | elderly det/n |")
    for r in types:
        a, e = r["adult"], r["elderly"]
        print(f"| {r['type']} {r['description'][:42]} | {a['detected']}/{a['windowed']} ({a['sensitivity']:.2f}) | {pct(a['sensitivity_without_confirmation'])} | {e['detected']}/{e['windowed']} |")
    print(f"injected: {k}/{n} wilson {wilson(k, n)[0]:.3f}-{wilson(k, n)[1]:.3f}; FA/pw {e2e['false_fall_alerts_per_patient_week']}; latency {lat['event_to_queued_s']} {lat['event_to_sent_s']}")
    print("inactivity:", {kk: ic[kk] for kk in ic if "per_patient_week" in kk}, "FA/h", round(noconf["overall_false_alarms_per_hour"], 2), "->", round(conf["overall_false_alarms_per_hour"], 2))


if __name__ == "__main__":
    main()
