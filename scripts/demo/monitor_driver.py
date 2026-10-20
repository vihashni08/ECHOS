"""Why is the MONITOR share what it is? Re-runs the (pure) triage rules over the stored replay agent outputs (data/e2e_replay/echos.db) with alternative
vitals_prob threshold. PROPOSES, never applies: config.yaml is not touched. Adds the out-of-fold recall cost from the ablation numbers.
Writes reports/metrics/monitor_driver.json and the block <!-- monitor_driver:start/end --> in reports/RESULTS.md.
Usage: PYTHONPATH=. python scripts/demo/monitor_driver.py   (after scripts/evaluation/e2e.py and scripts/evaluation/ablation.py)"""
from __future__ import annotations

import json
import warnings
from collections import Counter

import numpy as np
from sqlalchemy import select

warnings.filterwarnings("ignore")
from echos.config import ROOT, load_config  # noqa: E402
from echos.orchestrator import state as S  # noqa: E402
from echos.orchestrator.triage import triage  # noqa: E402

RESULTS = ROOT / "reports/RESULTS.md"
START, END = "<!-- monitor_driver:start -->", "<!-- monitor_driver:end -->"


def main():
    cfg = load_config()
    t = cfg["triage"]
    eng = S.get_engine(ROOT / "data/e2e_replay/echos.db")
    with eng.begin() as c:
        cases = c.execute(select(S.case_state)).fetchall()
        outs = c.execute(select(S.agent_output)).fetchall()
    by: dict = {}
    for o in outs:
        by.setdefault((o.patient_id, o.day), {}).setdefault(o.agent_id, []).append(json.loads(o.result))
    days = []
    for cs in cases:
        d = by.get((cs.patient_id, cs.day), {})
        fall = d.get("fall", [])
        res = {"quality": (d.get("quality") or [None])[-1], "health": (d.get("health_monitoring") or [None])[-1],
               "fall": max(fall, key=lambda o: o["score"]) if fall else {"score": 0.0, "evidence": {}, "flag": False}}
        days.append((cs, res))
    base = [triage(r, cs.days_since_vitals).status.value for cs, r in days]
    mism = sum(b != cs.status for b, (cs, _) in zip(base, days))
    n = len(days)

    def share(vp):
        tt = {**t, "vitals_prob": vp}
        st = Counter(triage(r, cs.days_since_vitals, tt).status.value for cs, r in days)
        return st["MONITOR"] / n, st["URGENT"] / n
    # driver attribution: MONITOR days that disappear when one trigger is switched off
    no_v, _ = share(2.0)
    cur, _ = share(t["vitals_prob"])
    drivers = {"current_monitor_share": cur, "without_vitals_trigger (data gap + possible fall only)": no_v}
    vit = np.array([(r["health"] or {}).get("vitals", {}).get("probability") for _, r in days if (r["health"] or {}).get("vitals", {}).get("available")])
    grid = []
    for vp in (t["vitals_prob"], 0.35, 0.40, 0.45, 0.50, 0.60):
        m, u = share(vp)
        grid.append({"vitals_prob": vp, "monitor_share": m, "vitals_alert_days": int((vit >= vp).sum()), "vitals_alerts_per_patient_week": float((vit >= vp).sum() / (n / 7))})
    abl = json.loads((ROOT / "reports/metrics/ablation.json").read_text())["tables"]["vitals_labelled_days"]
    rec = {k: v["B vitals model only"]["recall"] for k, v in abl.items() if "/ pt-wk" in k}
    target = next((g for g in grid[1:] if g["monitor_share"] <= 0.25), None)
    res = {"patient_days": n, "triage_reconstruction_mismatches": mism, "monitor_share_current": cur, "drivers": drivers, "vitals_grid": grid,
           "oof_vitals_recall_by_budget": rec, "proposal_not_applied": target,
           "notes": ["replay scores are out-of-fold; thresholds were chosen on the same OOF scores", "proposal only: config.yaml unchanged"]}
    (ROOT / "reports/metrics/monitor_driver.json").write_text(json.dumps(res, indent=1))
    md = [START, "## MONITOR share driver and threshold proposal (written by scripts/demo/monitor_driver.py; NOT applied)", "",
          f"Current replay MONITOR share {cur:.1%} of {n} days (limit I set myself: 25%). Triage re-run over stored agent outputs ({mism} mismatches vs the stored statuses).", "",
          "| Trigger switched off | MONITOR share |", "|---|---|"]
    md += [f"| none (current) | {cur:.1%} |", f"| vitals trigger (only data gaps / possible falls remain) | {no_v:.1%} |", "",
           "| vitals_prob | MONITOR share | vitals alert days | vitals alerts / patient-week |", "|---|---|---|---|"]
    md += [f"| {g['vitals_prob']} | {g['monitor_share']:.1%} | {g['vitals_alert_days']} | {g['vitals_alerts_per_patient_week']:.2f} |" for g in grid]
    md += ["", "Out-of-fold vitals recall at fixed budgets (ablation, B): " + ", ".join(f"{k} -> {v:.2f}" for k, v in rec.items()) + ".",
           ("**Proposal (not applied):** raise `triage.vitals_prob` to %s: MONITOR share %.1f%%, about %.2f vitals alerts per patient-week (recall cost: see the budget recalls above)."
            % (target["vitals_prob"], 100 * target["monitor_share"], target["vitals_alerts_per_patient_week"]) if target else "No vitals_prob in the grid reaches 25%."), END]
    s = RESULTS.read_text()
    block = "\n".join(md)
    s = s[:s.index(START)] + block + s[s.index(END) + len(END):] if START in s else s.rstrip() + "\n\n" + block + "\n"
    RESULTS.write_text(s)
    print(json.dumps({"mismatch": mism, "drivers": {k: round(v, 3) for k, v in drivers.items()}, "grid": [(g["vitals_prob"], round(g["monitor_share"], 3), round(g["vitals_alerts_per_patient_week"], 2)) for g in grid], "recall": rec, "proposal": target}))


if __name__ == "__main__":
    main()
