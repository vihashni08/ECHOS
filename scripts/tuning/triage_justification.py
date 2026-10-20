"""Numbers and doc for the decision theory behind Triage stage 1 (echos/orchestrator/triage_theory.py). Offline, read-only, no LLM / PubMed / e-mail.

E2  implied cost ratio per detector from its threshold, only as meaningful as the score is calibrated (Brier skill from reports/metrics/calibration.json)
E3  recall / precision at the budget-chosen threshold (reports/metrics/monitor_thresholds.json), fall: effect of the inactivity confirmation (fall_audit.json)
E4  alert load on the replayed days (data/e2e_replay/echos.db): measured share of days per source vs the independence prediction 1 - prod(1 - a_k)
E1  property sweep of the real triage() (monotone, a data gap is never NORMAL, critical vitals always URGENT, a missing output is never NORMAL)
Usage: PYTHONPATH=. python scripts/tuning/triage_justification.py -> reports/metrics/triage_justification.json, docs/TRIAGE_ALGORITHM.md"""
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("ECHOS_DRY_RUN", "1")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from echos.config import load_config                          # noqa: E402
from echos.orchestrator import triage_theory as TT            # noqa: E402
from scripts.evaluation import routing_eval as RE  # noqa: E402

M = ROOT / "reports/metrics"
J = lambda n: json.loads((M / n).read_text())                 # noqa: E731


def source_flags(reasons: list) -> dict:
    r = [str(x) for x in reasons]
    return {"vitals_model": any(x.startswith("vitals alert") for x in r),
            "possible_fall": any(x.startswith("possible fall") for x in r), "data_gap": any(x.startswith("DATA_GAP") for x in r),
            "urgent_fall": any(x.startswith("fall p=") for x in r), "urgent_vitals": any(x.startswith("critical vitals") for x in r)}


def build() -> dict:
    t, cal, mt, fa = load_config()["triage"], J("calibration.json"), J("monitor_thresholds.json")["new"], J("fall_audit.json")
    det = {}
    for name, key, tau, rec, prec in (("vitals", "vitals", t["vitals_prob"], mt["vitals_recall"], mt["vitals_precision"]),):
        c = cal[key]
        det[name] = {"tau": tau, "implied_miss_to_false_alert_cost_ratio": round(TT.implied_cost_ratio(tau), 2), "prevalence": round(c["prevalence"], 3),
                     "brier_calibrated": round(c["brier"][c["chosen_kind"]], 4), "brier_prevalence_baseline": round(c["brier_baseline_prevalence"], 4),
                     "brier_skill": round(TT.brier_skill(c["brier"][c["chosen_kind"]], c["brier_baseline_prevalence"]), 3),
                     "recall_at_tau": round(rec, 3), "precision_at_tau": None if prec is None else round(prec, 3)}
    det["vitals"]["alerts_per_patient_week"] = round(mt["vitals_alerts_per_patient_week"], 3)
    cur = {(r["threshold"], r["confirm_inactivity"]): r for r in fa["curve"]}
    u, c_ = cur[(0.8, False)], cur[(0.8, True)]
    fall = {"threshold": 0.8, "unconfirmed": {"sensitivity": round(u["adult_sensitivity"], 3), "false_alarms_per_hour": round(u["adult_false_alarms_per_hour"], 3)},
            "confirmed_by_inactivity": {"sensitivity": round(c_["adult_sensitivity"], 3), "false_alarms_per_hour": round(c_["adult_false_alarms_per_hour"], 3)},
            "false_alarm_reduction_factor": round(u["adult_false_alarms_per_hour"] / c_["adult_false_alarms_per_hour"], 2),
            "sensitivity_cost": round(u["adult_sensitivity"] - c_["adult_sensitivity"], 3),
            "note": "SisFall adult trials; ADL trials are short and vigorous, so false alarms per hour overstate daily life"}
    days = RE.load()
    n, pw = len(days), len(days) / 7.0
    fl = [source_flags(d["decision"]["reasons"]) for d in days]
    st = [d["decision"]["status"] for d in days]
    rate = {k: sum(f[k] for f in fl) / n for k in fl[0]}
    model_rates = [rate["vitals_model"], rate["possible_fall"], rate["data_gap"]]
    measured = sum(s == "MONITOR" for s in st) / n
    load = {"patient_days": n, "patient_weeks": round(pw, 1), "share_of_days_by_source": {k: round(v, 3) for k, v in rate.items()},
            "alerts_per_patient_week_by_source": {k: round(v * 7, 3) for k, v in rate.items()},
            "monitor_days_measured": round(measured, 3), "monitor_days_independence_prediction": round(TT.union_rate(model_rates), 3),
            "monitor_days_without_data_gap": round(sum(s == "MONITOR" and not f["data_gap"] for s, f in zip(st, fl)) / n, 3),
            "urgent_days_per_patient_week": round(sum(s == "URGENT" for s in st) / pw, 3),
            "budget_union_bound_model_alerts_per_patient_week": round(mt["vitals_alerts_per_patient_week"], 3)}
    return {"detectors": det, "fall": fall, "load": load, "properties": TT.check_properties(),
            "notes": ["Costs are IMPLIED by the thresholds (tau = c_fa/(c_fa+c_miss)); nothing here estimates the real cost of a missed event.",
                      "A cost reading of a threshold is only valid to the extent the score is calibrated: Brier skill is the evidence.",
                      "Event labels (Labels.csv) are of unverified provenance, so recall / precision are weak evidence; about 49 labelled patients: wide intervals."]}


def render(r: dict) -> str:
    d, f, ld, p = r["detectors"], r["fall"], r["load"], r["properties"]
    v = d["vitals"]
    ok = all(x == 0 for k, x in p.items() if k.startswith("violations_"))
    L = ["# How triage decides, and why it is justified", "",
         "Generated by `scripts/tuning/triage_justification.py` (numbers from `reports/metrics/*.json` and the replayed days); equations are implemented and tested in `echos/orchestrator/triage_theory.py`. "
         "Triage stage 1 (`echos/orchestrator/triage.py`) is a rule function, not a trained model. This page says why those rules are a sound way to decide, and where they are not.", "",
         "## The algorithm: severity = the maximum over independent rules", "",
         "```", "s(x) = max( s_urgent(x), s_monitor(x), s_gap(x) )          NORMAL < MONITOR < URGENT", "",
         "s_urgent : fall score >= 0.8 AND post-fall inactivity      OR   a vital sign beyond its absolute clinical limit",
         "s_monitor: vitals score >= tau_v   OR   an unconfirmed fall",
         "s_gap    : quality unchecked / output missing / monitoring degraded / no vitals for > 7 days      (never NORMAL)", "```", "",
         "**E1, why a maximum.** Each rule asks one question about one source, so a new source or rule can only add evidence, never hide it. This gives properties that can be checked rather than argued. "
         f"On a grid of {p['grid_points']} input combinations, run through the real `triage()`: raising any score or switching on a flag never lowered the status "
         f"({p['violations_monotone']} violations); a data gap was never NORMAL ({p['violations_gap_is_normal']}); a critical vital was always URGENT whatever the model score ({p['violations_critical_not_urgent']}); "
         f"a missing detector output was never NORMAL ({p['violations_missing_output_is_normal']}). All hold = {ok}.", "",
         "## Why a threshold on a calibrated score", "",
         "For a detector with a calibrated score p = P(event | x), alerting has the lower expected cost exactly when", "",
         "```", "p * c_miss  >  (1 - p) * c_fa      <=>      p  >  tau = c_fa / (c_fa + c_miss)      <=>      c_miss / c_fa = (1 - tau) / tau", "```", "",
         "(E2; verified numerically in `tests/test_triage_theory.py`: on synthetic calibrated scores the cost-minimising threshold equals c_fa/(c_fa+c_miss).) "
         "ECHOS does not know the real costs, so it fixes the false-alert budget instead and reads the cost off the threshold (E3, the Neyman-Pearson form: the highest recall at a fixed false-alert rate is a threshold on the score):", "",
         "```", "tau_k = min { t : FA_k(t) <= B_k }          vitals: B <= 1 alert / patient-week", "```", "",
         "| Detector | threshold tau | implied c_miss / c_fa | Brier skill (calibrated) | recall at tau | precision at tau |", "|---|---|---|---|---|---|",
         f"| Vitals | {v['tau']} | {v['implied_miss_to_false_alert_cost_ratio']} | {v['brier_skill']} | {v['recall_at_tau']} | {v['precision_at_tau']} |",
         "",
         f"Reading: at the chosen budget the vitals rule treats one missed event as worth about {v['implied_miss_to_false_alert_cost_ratio']} false alerts. "
         f"Brier skill = 1 - Brier / Brier(prevalence): vitals {v['brier_skill']} (the score is informative and can be read as a probability). ",
         "These costs are implied, not measured: nothing here estimates what a missed event really costs.", "",
         "## Why a fall needs inactivity as well", "",
         f"The fall detector scores a 2.5 s window; most false alarms are vigorous daily movement. Requiring post-fall inactivity (rule `fall >= {f['threshold']} AND inactive`) trades a little sensitivity for far fewer false alarms: "
         f"at the same threshold sensitivity goes {f['unconfirmed']['sensitivity']} -> {f['confirmed_by_inactivity']['sensitivity']} (cost {f['sensitivity_cost']}), false alarms per hour {f['unconfirmed']['false_alarms_per_hour']} -> {f['confirmed_by_inactivity']['false_alarms_per_hour']} "
         f"(x{f['false_alarm_reduction_factor']} fewer). An unconfirmed fall is still MONITOR, so it is not lost. {f['note']}.", "",
         "## Alert load: what the budget does and does not bound", "",
         "If detectors raise alerts independently with day-rates a_k, the share of days with at least one alert and the weekly load are", "",
         "```", "a_day = 1 - prod_k (1 - a_k)          alerts / patient-week  <=  sum_k B_k          (E4: independence, union bound)", "```", "",
         f"On the {ld['patient_days']} replayed days ({ld['patient_weeks']} patient-weeks): the model alerts are bounded by {ld['budget_union_bound_model_alerts_per_patient_week']} per patient-week by the budgets. "
         f"Measured share of days by source: " + ", ".join(f"{k.replace('_', ' ')} {x}" for k, x in ld["share_of_days_by_source"].items()) + ". "
         f"MONITOR days measured {ld['monitor_days_measured']} vs independence prediction {ld['monitor_days_independence_prediction']} (close, so independence is a fair approximation here); "
         f"without a data gap {ld['monitor_days_without_data_gap']} of days are MONITOR, so the load comes from the model alerts, and data gaps add only {ld['share_of_days_by_source']['data_gap']} of days. "
         f"The vitals rule raised {ld['alerts_per_patient_week_by_source']['vitals_model']} alerts per patient-week on these days against its budget of {v['alerts_per_patient_week']} measured on out-of-fold data: "
         f"the replay is a subset (20 patients, {ld['patient_days']} of the {J('threshold_holdout.json')['patient_days']} patient-days the budget was set on), so the budget holds on the data it was set on and is exceeded on this subset: a budget is a design target, not a guarantee. "
         f"URGENT: {ld['urgent_days_per_patient_week']} days per patient-week, falls INJECTED from SisFall in the replay (TIHM has no falls) (urgent fall {ld['share_of_days_by_source']['urgent_fall']} of days), so this figure is a test load, not a rate of real falls.", "",
         "## What this does not show", ""]
    L += [f"- {x}" for x in r["notes"]]
    L += ["- The rules, the budgets and the inactivity requirement are design choices backed by the numbers above; none is validated against real clinical outcomes.", ""]
    return "\n".join(L)


if __name__ == "__main__":
    res = build()
    (M / "triage_justification.json").write_text(json.dumps(res, indent=1))
    (ROOT / "docs/TRIAGE_ALGORITHM.md").write_text(render(res))
    print(json.dumps({"properties": res["properties"], "vitals": res["detectors"]["vitals"], "fall": res["fall"], "load": res["load"]}, indent=1))
