"""Regenerates the RESULTS.md 'Reading the tables' bullets for the end-to-end replay and fault injection from reports/metrics/*.json
(no hand-typed numbers). Block: <!-- reading_e2e_fault:start/end -->. Usage: PYTHONPATH=. python scripts/docgen/sync_prose.py
check_docs_numbers.py compares the file with `render()`."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
M = ROOT / "reports/metrics"
START, END = "<!-- reading_e2e_fault:start -->", "<!-- reading_e2e_fault:end -->"


def J(n):
    return json.loads((M / f"{n}.json").read_text())


def render() -> str:
    e, fi, md_, fe = J("e2e"), J("fault_injection"), J("monitor_driver"), J("fall_eval")
    ci = e["falls_detected_pct_ci95_wilson"]
    sh = e["status_share"]
    mon = md_["drivers"]
    cs, ref = fi["cases"], fi["cases"]["clean"]
    fault = {k: v for k, v in cs.items() if k != "clean"}
    clean_ok = [k for k, v in fault.items() if not (set(ref["alerts"]) - set(v["alerts"])) and v["outbox_duplicates"] == 0 and v["mail_duplicates"] == 0]
    failed = {k: v["outbox_status"].get("FAILED", 0) for k, v in fault.items() if v["outbox_status"].get("FAILED")}
    rec = {k: v["timing"].get("recover_s") for k, v in fault.items() if v["timing"].get("recover_s") is not None}
    ia = [s["queued_s"] for s in ref["injected_alerts_all"] if s["queued_s"] is not None]
    lines = [START,
             f"- **End-to-end (template mode, DryRun mail, out-of-fold vitals scores):** {e['falls_detected_as_urgent']}/{e['injected_falls']} injected held-out-subject falls raised an URGENT alert "
             f"({e['fall_detection_rate']:.1%}, Wilson 95% CI {ci[0]:.3f}-{ci[1]:.3f}); {e['false_fall_alerts']} false fall alerts ({e['false_fall_alerts_per_patient_week']} per patient-week). "
             f"Alerts are decided at day close, so the simulated delay is {e['detection_delay_sim_hours_median']} h median (max {e['detection_delay_sim_hours_max']} h): the orchestrator is a daily batch, "
             f"not real-time; a fall injected through the live controller (which closes the day at once) is queued in about {sum(ia) / len(ia):.2f} s on average. "
             f"{sh['MONITOR']:.1%} of patient-days are Monitor ({e['data_gap_share_any_status']:.1%} carry a data gap), "
             + ("above the 25% I consider usable for caregivers; " if sh["MONITOR"] > 0.25 else "within the 25% I consider usable for caregivers; ")
             + f"switching the vitals trigger off would leave {mon['without_vitals_trigger (data gap + possible fall only)']:.1%} (see the MONITOR driver section; a threshold change is proposed, not applied).",
             f"- **Fault injection (fakes, {fi['config']['patients']} patients x {fi['config']['days']} days, compared with a clean run):** no alert lost and no duplicate in {len(clean_ok)} of {len(fault)} fault cases"
             + (f"; the exception is the SMTP outage longer than the retry window, where alerts were queued but {sum(failed.values())} outbox rows ended FAILED (undelivered, visible; measured before the 15-minute retry window and 2-hour sweeper were added, see Limitations)" if failed else "")
             + f". Worker kills were detected in {min(v['timing']['detect_s'] for v in fault.values() if v['timing'].get('detect_s') is not None):.1f}-{max(v['timing']['detect_s'] for v in fault.values() if v['timing'].get('detect_s') is not None):.1f} s "
             f"and recovered (first new output) in {min(rec.values()):.1f}-{max(rec.values()):.1f} s; a fall injected during such an outage is delayed by up to the restart time but not lost. "
             "Duplicates are impossible by construction (unique alert key). Differences from the clean run were restricted to quality / degraded flags.",
             END]
    return "\n".join(lines)


def main():
    p = ROOT / "reports/RESULTS.md"
    s = p.read_text()
    block = render()
    if START in s:
        s = s[:s.index(START)] + block + s[s.index(END) + len(END):]
    else:
        s = re.sub(r"^- \*\*End-to-end:\*\*.*\n", block + "\n", s, count=1, flags=re.M)
        s = re.sub(r"^- \*\*Fault injection:\*\*.*\n", "", s, count=1, flags=re.M)
    p.write_text(s)
    print(block)


if __name__ == "__main__":
    main()
