"""Fail if headline numbers in README.md / reports/RESULTS.md disagree with reports/metrics/*.json.

1. every line of the generated tables (scripts/evaluate.py build()) must appear verbatim in RESULTS.md;
2. README headline bullets and RESULTS 'MONITOR thresholds' table are re-derived from the JSONs and must match.
Usage: PYTHONPATH=. python3 scripts/check_docs_numbers.py   (exit 1 + list of mismatches)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
M = ROOT / "reports/metrics"


def J(name):
    return json.loads((M / f"{name}.json").read_text())


def check_fault_table(results: str) -> list[str]:
    """RESULTS.md fault-injection table (10 columns, pre-Agent-0 run) must equal reports/metrics/fault_injection_pre_agent0.json
    (= commit 62669da). fault_injection.json is overwritten by every new run, so it is not compared."""
    fi = J("fault_injection_pre_agent0")
    c = fi["config"]
    d = lambda v: f"{v:.1f}" if v is not None else "-"
    want = [f"### Fault injection ({c['patients']} patients x {c['days_each']} days; clean run {c['ref_wall_s']} s)"]
    want += [f"| {r['scenario']} | {r['lost_alerts']} | {r['extra_alerts']} | {r['duplicate_alerts']} | {r['falls_alerted']} | {d(r['detect_s'])} | "
             f"{d(r['recover_s'])} | {r['cases_identical_to_clean_run']} | {','.join(f'{k}:{v}' for k, v in r['report_modes'].items())} | {r['wall_overhead_s']} |"
             for r in fi["rows"]]
    return [f"RESULTS.md missing fault-injection line: {w[:110]}" for w in want if w not in results]


def check() -> list[str]:
    import os
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    from scripts import evaluate
    evaluate.rows.clear()
    results, readme = (ROOT / "reports/RESULTS.md").read_text(), (ROOT / "README.md").read_text()
    bad = []
    generated = evaluate.build().split("### Fault injection")[0]  # fault table is checked below against the frozen pre-Agent-0 run
    for line in generated.splitlines():
        if line.strip() and line not in results:
            bad.append(f"RESULTS.md missing generated line: {line[:110]}")
    bad += check_fault_table(results)
    a1, a2, a3, e, mt = J("agent1"), J("agent2"), J("agent3"), J("e2e"), J("monitor_thresholds")
    x, rule = a1["results"]["xgboost"], a1["results"]["fixed_rule"]
    g = a3["groups"]
    sh = e["status_share"]
    wp = a2["within_patient_audit"]["full"]["within_patient"]["roc_auc_median_q1_q3"][0]
    want = {
        "Agent 1 PR-AUC": f"PR-AUC {x['pr_auc']:.2f} vs {rule['pr_auc']:.2f}",
        "Agent 2 PR-AUC / base rate": f"PR-AUC {a2['point']['pr_auc']:.2f} vs {a2['point']['prevalence']:.2f} base rate",
        "Agent 2 within-patient ROC": f"within-patient ROC-AUC {wp:.2f}",
        "Agent 3 sensitivity": f"sensitivity {g['adult']['sensitivity']:.3f} adults / {g['elderly']['sensitivity']:.3f} elderly",
        "e2e detection": f"{e['falls_detected_as_urgent']} detected ({e['fall_detection_rate']:.0%})",
        "e2e injected": f"{e['patients']} patients, {e['injected_falls']} injected falls",
        "e2e shares": f"days are {sh['NORMAL']:.0%} Normal / {sh['MONITOR']:.0%} Monitor / {sh['URGENT']:.0%} Urgent",
    }
    for name, s in want.items():
        if s not in readme:
            bad.append(f"README.md: {name}: expected text not found: '{s}'")
    n, o = mt["new"], mt["old"]
    for name, s in {"vitals_prob": f"| vitals_prob | {o['vitals_prob']} | {n['vitals_prob']} |",
                    "agitation_prob": f"| agitation_prob | {o['agitation_prob']} | {n['agitation_prob']} |",
                    "behaviour monitor recall": f"| Agitation recall of behaviour Monitor | {o['behaviour_monitor_recall']:.3f} | {n['behaviour_monitor_recall']:.3f} |",
                    "behaviour monitor share": f"| Behaviour Monitor days, share of patient-days | {o['behaviour_monitor_share_of_patient_days']:.1%} | {n['behaviour_monitor_share_of_patient_days']:.1%} |"}.items():
        if s not in results:
            bad.append(f"RESULTS.md MONITOR table: {name}: expected row not found: '{s}'")
    import yaml
    t = yaml.safe_load((ROOT / "config.yaml").read_text())["triage"]
    if (t["vitals_prob"], t["agitation_prob"]) != (n["vitals_prob"], n["agitation_prob"]):
        bad.append(f"config.yaml triage thresholds {t['vitals_prob']}/{t['agitation_prob']} != monitor_thresholds.json {n['vitals_prob']}/{n['agitation_prob']}")
    return bad


if __name__ == "__main__":
    bad = check()
    print("docs numbers OK" if not bad else "\n".join(bad))
    sys.exit(1 if bad else 0)
