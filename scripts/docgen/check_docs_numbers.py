"""Fail if headline numbers in README.md / reports/RESULTS.md disagree with reports/metrics/*.json.

1. every line of the generated tables (scripts/evaluation/evaluate.py build()) must appear verbatim in RESULTS.md;
2. README headline bullets and RESULTS 'MONITOR thresholds' table are re-derived from the JSONs and must match.
Usage: PYTHONPATH=. python3 scripts/docgen/check_docs_numbers.py   (exit 1 + list of mismatches)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
M = ROOT / "reports/metrics"


def J(name):
    return json.loads((M / f"{name}.json").read_text())


def check_fault_table(results: str) -> list[str]:
    """The fault-injection table in RESULTS.md must equal the one rendered from reports/metrics/fault_injection.json."""
    from scripts.evaluation.fault_report import table
    want = []
    new = J("fault_injection")
    want += table(new).splitlines() if "cases" in new else []
    return [f"RESULTS.md missing fault-injection line: {w[:110]}" for w in want if w not in results]


def check_blocks(results: str) -> list[str]:
    """Headline numbers of the script-written blocks (fall_eval, ablation, gate_eval, e2e) must match their JSON."""
    bad = []
    fe, ab, ge = J("fall_eval"), J("ablation"), J("gate_eval")
    ie = fe["injected_end_to_end"]
    for name, s in {"fall_eval injected": f"| Detected as URGENT fall alert | {ie['detected']}/{ie['injected']} = {ie['detection_rate']:.3f} (Wilson 95% CI {ie['wilson95'][0]:.3f}-{ie['wilson95'][1]:.3f}) |",
                    "gate_eval routed": f"| Daily reports routed to the LLM | {ge['reports_routed_to_llm']['gate_on']} | {ge['reports_routed_to_llm']['always_llm']} | {ge['reports_routed_to_llm']['avoided']} ({ge['reports_routed_to_llm']['avoided_share']:.1%} of days) |",
                    "gate_eval missed": f"alerts missed due to gating: **{ge['alerts_missed_due_to_gating']}**"}.items():
        if s not in results:
            bad.append(f"RESULTS.md {name}: expected text not found: '{s[:100]}'")
    for lab, sy in ab["tables"]["any_labelled_day"].items():
        row = f"| {lab} | {sy['alerts_per_pw']:.2f} | {sy['recall']:.2f} ({sy['recall_ci95'][0]:.2f}-{sy['recall_ci95'][1]:.2f}) |"
        if row not in results:
            bad.append(f"RESULTS.md ablation row missing: {row[:100]}")
    from scripts.docgen.sync_prose import render
    if render() not in results:
        bad.append("RESULTS.md: the 'Reading the tables' end-to-end / fault-injection block differs from scripts/docgen/sync_prose.py (re-run it)")
    e = J("e2e")
    for k, v in e["monitor_days_by_reason"].items():
        if f"| {k} | {v} | {v / sum(e['monitor_days_by_reason'].values()):.1%} |" not in results:
            bad.append(f"RESULTS.md e2e MONITOR reason row missing: {k}")
    return bad


def check() -> list[str]:
    import os
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    from scripts.evaluation import evaluate
    evaluate.rows.clear()
    results, readme = (ROOT / "reports/RESULTS.md").read_text(), (ROOT / "README.md").read_text()
    bad = []
    generated = evaluate.build().split("### Fault injection")[0]  # fault table is checked below against the frozen run before the Data Quality agent
    for line in generated.splitlines():
        if line.strip() and line not in results:
            bad.append(f"RESULTS.md missing generated line: {line[:110]}")
    bad += check_fault_table(results)
    bad += check_blocks(results)
    a1, a3, e, mt = J("agent1"), J("agent3"), J("e2e"), J("monitor_thresholds")
    x, rule = a1["results"]["xgboost"], a1["results"]["fixed_rule"]
    g = a3["groups"]
    sh = e["status_share"]
    want = {
        "vitals PR-AUC": f"PR-AUC {x['pr_auc']:.2f} vs {rule['pr_auc']:.2f}",
        "fall sensitivity": f"sensitivity {g['adult']['sensitivity']:.3f} adults / {g['elderly']['sensitivity']:.3f} elderly",
        "e2e detection": f"{e['falls_detected_as_urgent']} detected ({e['fall_detection_rate']:.0%})",
        "e2e injected": f"{e['patients']} patients, {e['injected_falls']} injected falls",
        "e2e shares": f"days are {sh['NORMAL']:.0%} Normal / {sh['MONITOR']:.0%} Monitor / {sh['URGENT']:.0%} Urgent",
    }
    for name, s in want.items():
        if s not in readme:
            bad.append(f"README.md: {name}: expected text not found: '{s}'")
    n, o = mt["new"], mt["old"]
    for name, s in {"vitals_prob": f"| vitals_prob | {o['vitals_prob']} | {n['vitals_prob']} |"}.items():
        if s not in results:
            bad.append(f"RESULTS.md MONITOR table: {name}: expected row not found: '{s}'")
    import yaml
    t = yaml.safe_load((ROOT / "config.yaml").read_text())["triage"]
    if t["vitals_prob"] != n["vitals_prob"]:
        bad.append(f"config.yaml triage threshold {t['vitals_prob']} != monitor_thresholds.json {n['vitals_prob']}")
    from scripts.docgen.make_research_gaps import build as gaps
    want_gaps = gaps()
    if want_gaps not in readme:
        bad.append("README.md: the 'Research gaps' table differs from scripts/docgen/make_research_gaps.py (run it): includes the report-text proxy numbers")
    if want_gaps not in (ROOT / "docs/RESEARCH_GAPS.md").read_text():
        bad.append("docs/RESEARCH_GAPS.md differs from scripts/docgen/make_research_gaps.py (run it)")
    return bad


if __name__ == "__main__":
    bad = check()
    print("docs numbers OK" if not bad else "\n".join(bad))
    sys.exit(1 if bad else 0)
