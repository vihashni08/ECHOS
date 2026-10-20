"""Pure helpers for the fault-injection table (no heavy imports: used by scripts/evaluation/fault_injection.py and scripts/docgen/check_docs_numbers.py)."""
from __future__ import annotations

import json

SUP_RESTART_DELAY_S = 10.0     # assumption: an operator / service manager restarts a dead supervisor after 10 s
SMTP_OUTAGE_S = 8.0

CASES = ["clean", "kill_quality", "kill_health_monitoring", "kill_fall", "kill_supervisor", "kill_supervisor_and_fall", "llm_errors", "llm_timeout",
         "pubmed_failing", "smtp_transient", "smtp_outage", "api_restart"]
LABEL = {"clean": "clean run (reference)", "kill_quality": "kill quality worker", "kill_health_monitoring": "kill health_monitoring worker",
         "kill_fall": "kill fall worker", "kill_supervisor": "kill supervisor (workers keep running)",
         "kill_supervisor_and_fall": f"kill supervisor, then fall worker; supervisor restarted after {SUP_RESTART_DELAY_S:g} s",
         "llm_errors": "LLM raising errors (HTTP 503 on every call)", "llm_timeout": "LLM timing out (every call)", "pubmed_failing": "PubMed failing (HTTP 503)",
         "smtp_transient": "SMTP fails twice per recipient, then recovers", "smtp_outage": f"SMTP down for {SMTP_OUTAGE_S:g} s (longer than the 3-attempt retry window)",
         "api_restart": "API process killed mid-run, restarted after 2 s"}



def split_cases(ref: dict, run: dict):
    """identical / differing only in a quality or degraded flag / other."""
    ident = qual = other = 0
    for k, (rs, rr) in ref["cases"].items():
        ns, nr = run["cases"].get(k, (None, None))
        if (ns, nr) == (rs, rr):
            ident += 1
            continue
        a, b = set(json.loads(rr or "[]")), set(json.loads(nr or "[]"))
        extra, missing = b - a, a - b
        flag = lambda x: "quality" in x.lower() or "degraded" in x.lower()   # noqa: E731
        if ns is not None and extra and all(flag(x) for x in extra) and (not missing or rs == "NORMAL" or all(flag(x) for x in missing)):
            qual += 1
        else:
            other += 1
    return ident, qual, other


def table(d: dict) -> str:
    ref = d["cases"].get("clean")
    rows = ["| Fault | Alerts queued (clean) | Lost / extra | Duplicates (outbox / mail) | Delivered (SENT) / FAILED rows | Falls alerted | Detect (s) | Recover (s) | Injected fall: delay added, queued / sent (s) | Cases identical / quality-flag only / other | Report modes |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name in CASES:
        r = d["cases"].get(name)
        if not r:
            continue
        t, ia = r.get("timing", {}), r.get("injected_alert")
        if ref:
            lost = f"{len(set(ref['alerts']) - set(r['alerts']))} / {len(set(r['alerts']) - set(ref['alerts']))}"
            ident, qual, oth = split_cases(ref, r)
            ci = f"{ident} / {qual} / {oth}"
            da = (f"{ia['queued_s'] - ref['injected_alert']['queued_s']:+.2f} / " + (f"{ia['sent_s'] - ref['injected_alert']['sent_s']:+.2f}" if ia["sent_s"] is not None and ref["injected_alert"]["sent_s"] is not None else "not sent")) \
                if ia and ref.get("injected_alert") else "-"
        else:
            lost, ci, da = "-", "-", "-"
        det = "-" if t.get("detect_s") is None else f"{t['detect_s']:.1f}"
        rec = "-" if t.get("recover_s") is None else f"{t['recover_s']:.1f}"
        if name == "kill_supervisor":
            det = rec = "n/a (no worker died)"
        if name == "api_restart" and t.get("api_downtime_s") is not None:
            det, rec = "-", f"API back after {t['api_downtime_s']:.1f}"
        st = r["outbox_status"]
        rows.append(f"| {r['label']} | {len(r['alerts'])} ({len(ref['alerts']) if ref else '-'}) | {lost} | {r['outbox_duplicates']} / {r['mail_duplicates']} | "
                    f"{st.get('SENT', 0)} / {st.get('FAILED', 0)} | {r['falls_alerted']} | {det} | {rec} | {da} | {ci} | "
                    + ", ".join(f"{k}:{v}" for k, v in r["report_modes"].items()) + " |")
    return "\n".join(rows)


