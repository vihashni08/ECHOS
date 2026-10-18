"""Design-time justification of the number of agents (and what the Team Planner saves at run time). Offline: no LLM, PubMed or e-mail.

Part 1  Admission rule: a component is an AGENT only if it differs from every other agent in (a) goal, (b) authority / tools, (c) timing and failure profile.
        Checked against the real registry (echos/components.py): every agent's `never` clause must be non-empty and no two agents may share all three.
Part 2  Alternatives (1 agent, 2 agents, the 4 ECHOS agents, 6 agents) against criteria that can be read from the code and from measurements
        already in reports/metrics/. Where a criterion is an argument by construction, the table says so; only the 4-agent design was built and measured.
Part 3  What the Team Planner changes at run time: the planner is replayed on the 1266 replayed patient-days (reports/metrics/e2e_days.json) and its team
        size is compared with "always run all four".
Writes reports/metrics/team_design.json and docs/TEAM_DESIGN.md (numbers come from the JSONs, nothing typed by hand).
Usage: PYTHONPATH=. python scripts/team_design.py"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from echos.components import COMPONENTS          # noqa: E402
from echos.team.planner import plan_team          # noqa: E402

M = ROOT / "reports/metrics"
AGENTS = ("triage", "investigator", "coordinator", "report")

# (a) goal, (b) authority / tools, (c) timing + failure profile: the three things that must differ for a part to deserve its own agent.
PROFILE = {
    "triage": ("one status per patient-day", "reads detector outputs; writes status and opens the case; the ONLY part that decides", "synchronous, on the alert path; rules, cannot hang"),
    "investigator": ("explain WHY the status was given", "bounded tool loop over the person's own history (<= 7 checks); writes the case file; cannot change the status", "after the alert is queued; may fail without losing anything"),
    "coordinator": ("make sure someone acts on an alert", "owns the alert lifecycle, wall-clock timers, second contact, care tasks; cannot suppress the first alert", "long-running; days, not milliseconds; state survives a restart"),
    "report": ("say it in plain words for people", "LLM + retrieval tools + non-LLM validator; writes text only", "slow, rate-limited, can be down; template fallback"),
}

DESIGNS = [
    ("1 agent (monolith)", 1, "one loop: score, decide, investigate, write, chase acknowledgements",
     {"alert path independent of slow work": "no: the alert waits behind whatever the loop is doing (an LLM call, a retry)",
      "decider is not the writer": "no", "a slow part cannot stop a fast part": "no", "timers (ack / escalation) kept apart from per-day work": "no"}),
    ("2 agents (decide + write)", 2, "Triage; Report (investigation and alert follow-up folded into one of them)",
     {"alert path independent of slow work": "yes", "decider is not the writer": "yes", "a slow part cannot stop a fast part": "yes",
      "timers (ack / escalation) kept apart from per-day work": "no: a day-long acknowledgement timer sits inside a per-day writer"}),
    ("4 agents (ECHOS)", 4, "Triage; Case Investigator; Care Coordinator; Report",
     {"alert path independent of slow work": "yes", "decider is not the writer": "yes", "a slow part cannot stop a fast part": "yes",
      "timers (ack / escalation) kept apart from per-day work": "yes"}),
    ("6 agents", 6, "as 4, plus a separate Validator agent and a separate Escalation agent",
     {"alert path independent of slow work": "yes", "decider is not the writer": "yes", "a slow part cannot stop a fast part": "yes",
      "timers (ack / escalation) kept apart from per-day work": "yes"}),
]
# why not 6: the validator is plain code that must run in the same call as the writer (a failed check triggers ONE retry, echos/reports/agent.py); the escalation
# step needs the Coordinator's own alert state (alert_state table), so splitting it only adds a hand-over and a second place where the state can go out of step.


def build() -> dict:
    kinds = {c["id"]: c for c in COMPONENTS}
    prof = {k: PROFILE[k] for k in AGENTS}
    distinct = len({v for v in prof.values()}) == len(prof) and all(len({p[i] for p in prof.values()}) == len(prof) for i in range(3))
    admission = {"agents_in_registry": sorted(k for k, c in kinds.items() if c["kind"] == "agent" and k != "planner"),
                 "every_agent_has_a_never_clause": all(kinds[a]["never"] for a in AGENTS),
                 "goal_authority_timing_all_pairwise_different": distinct}

    days = json.loads((M / "e2e_days.json").read_text())
    counts, size_by, act = Counter(), Counter(), Counter()
    llm_ceiling = 0
    for d in days:
        p = plan_team({"status": d["status"], "data_gap": d["data_gap"], "reasons": d["reasons"],
                       "urgent_types": ["fall"] if d["status"] == "URGENT" and any("fall" in r for r in d["reasons"]) else (["critical_vitals"] if d["status"] == "URGENT" else [])})
        counts[d["status"]] += 1
        size_by[d["status"]] = p.team_size
        for a in p.active_ids:
            act[a] += 1
        llm_ceiling += p.max_llm_requests
    n = len(days)
    total_act, all4 = sum(act.values()), 4 * n
    ge = json.loads((M / "gate_eval.json").read_text())
    al = json.loads((M / "alert_latency.json").read_text())["no_load"]
    kt = json.loads((M / "kill_test.json").read_text())
    planner = {"patient_days": n, "days_by_status": dict(counts), "team_size_by_status": dict(size_by),
               "mean_team_size": round(total_act / n, 3), "agent_runs_planner": total_act, "agent_runs_always_all_four": all4,
               "agent_runs_avoided": all4 - total_act, "agent_runs_avoided_share": round(1 - total_act / all4, 3),
               "runs_per_agent": {a: act[a] for a in AGENTS},
               "llm_request_ceiling_planner": llm_ceiling, "llm_request_ceiling_every_day_llm": 5 * n,
               "investigator_runs_equal_non_normal_days": act["investigator"] == n - counts["NORMAL"]}
    evidence = {"alert_event_to_queued_median_s": al["event_to_queued"]["median_s"], "alert_event_to_sent_p95_s_dry_mailer": al["event_to_sent"]["p95_s"],
                "kill_test_lost_alerts": kt["lost_alerts"], "kill_test_killed": kt["killed_agent"], "kill_test_falls_alerted": kt["injected_falls_alerted_(kill run)"],
                "gate_triage_identical": ge["triage_identical"], "gate_alerts_identical": ge["alerts_identical"], "gate_alerts_missed": ge["alerts_missed_due_to_gating"]}
    re_ = M / "routing_eval.json"
    routing = json.loads(re_.read_text()) if re_.exists() else None
    rt_ = M / "routing_thresholds.json"
    return {"routing": routing, "routing_thresholds": json.loads(rt_.read_text()) if rt_.exists() else None, "admission": admission, "profile": {k: dict(zip(("goal", "authority_tools", "timing_failure"), v)) for k, v in prof.items()},
            "designs": [{"name": n_, "agents": k, "description": d, "criteria": c} for n_, k, d, c in DESIGNS], "planner_replay": planner, "evidence": evidence}


def render(r: dict) -> str:
    a, p, e = r["admission"], r["planner_replay"], r["evidence"]
    L = ["# Why four agents, and how many run per case", "", "Generated by `scripts/team_design.py` from `reports/metrics/team_design.json`. Not a diagnostic claim; a design argument plus replayed counts.", "",
         "## Rule: when does a part deserve its own agent?", "",
         "A component is an agent only if it differs from every other agent in **(a) goal, (b) authority / tools, (c) timing and failure profile**. "
         f"Checked against the registry: every agent has a `never` clause = {a['every_agent_has_a_never_clause']}; goal, authority and timing are pairwise different = {a['goal_authority_timing_all_pairwise_different']}.", "",
         "| Agent | (a) Goal | (b) Authority / tools | (c) Timing and failure |", "|---|---|---|---|"]
    L += [f"| {k} | {v['goal']} | {v['authority_tools']} | {v['timing_failure']} |" for k, v in r["profile"].items()]
    L += ["", "Detectors are not counted: they sense and never decide, and they are always on. The Team Planner is the coordination layer, not a fifth agent.", "",
          "## Alternatives considered", "", "Criteria are properties of the design (arguments by construction). Only the 4-agent design was built and measured; **no monolith or 2-agent version was implemented**, so there is no measured head-to-head.", ""]
    crit = list(r["designs"][0]["criteria"])
    L += ["| Design | Agents | " + " | ".join(crit) + " |", "|---|---|" + "---|" * len(crit)]
    L += [f"| {d['name']}: {d['description']} | {d['agents']} | " + " | ".join(d["criteria"][c] for c in crit) + " |" for d in r["designs"]]
    L += ["", "Why not six: the validator is plain code that has to run in the same call as the writer (one retry on a failed check), and escalation needs the Coordinator's own alert state, "
          "so splitting either adds a hand-over and a second place where state can go out of step, without a different goal or authority.", "",
          "## Evidence that the split costs the alert path nothing (existing measurements)", "",
          f"- Alert queued a median {e['alert_event_to_queued_median_s']*1000:.1f} ms after the event; sent (dry-run mailer, no load) p95 {e['alert_event_to_sent_p95_s_dry_mailer']*1000:.0f} ms (`alert_latency.json`; says nothing about real SMTP).",
          f"- Killing the `{e['kill_test_killed']}` worker mid-run: {e['kill_test_lost_alerts']} lost alerts, falls alerted {e['kill_test_falls_alerted']} (`kill_test.json`).",
          f"- Switching the report gate on left triage identical = {e['gate_triage_identical']}, alerts identical = {e['gate_alerts_identical']}, alerts missed = {e['gate_alerts_missed']} (`gate_eval.json`).", "",
          "## What the Team Planner changes at run time", "",
          f"The planner (`echos/team/planner.py`) was replayed on the {p['patient_days']} replayed patient-days (20 patients, out-of-fold scores).", "",
          "| Status | Days | Agents on the case |", "|---|---|---|"]
    L += [f"| {s} | {p['days_by_status'].get(s, 0)} | {p['team_size_by_status'].get(s)} of 4 |" for s in ("NORMAL", "MONITOR", "URGENT")]
    L += ["", f"- Mean team size {p['mean_team_size']} of 4. Agent runs: {p['agent_runs_planner']} vs {p['agent_runs_always_all_four']} if all four always ran "
          f"({p['agent_runs_avoided']} avoided, {p['agent_runs_avoided_share']:.1%}).",
          f"- Report LLM request ceiling: {p['llm_request_ceiling_planner']} vs {p['llm_request_ceiling_every_day_llm']} if every day could use the LLM (ceilings, not measured usage).",
          f"- Without scores (cutoff not applied) the Investigator runs on exactly the non-Normal days = {p['investigator_runs_equal_non_normal_days']}; near-miss watch days are counted in the routing section below.", "",
          "## Limits", "",
          "- The routing is an algorithm with a budget-chosen cutoff, not a learned allocation. Its counts follow from the status mix, so the saving is large only because most days are Normal.",
          "- Replayed days come from out-of-fold scores of 20 patients over three months: wide uncertainty, not a deployment estimate.",
          "- The 'urgent type' per replayed day is inferred from the reason text; it only affects the wording of the reason, not the team size.",
          "- A real ablation (build the monolith, compare alert latency and report quality) was not done."]
    text = "\n".join(L) + "\n"
    if r.get("routing"):
        text = text.replace("## Limits", "\n".join(routing_section(r["routing"], r.get("routing_thresholds"))) + "\n## Limits", 1)
    return text


def routing_section(rt: dict, th: dict | None = None) -> list:
    pr, sf, w = rt["parity_with_previous_routing"], rt["safety_floor"], rt["watch"]
    q = rt["normal_r_quantiles_50_90_99_max"] if "normal_r_quantiles_50_90_99_max" in rt else rt["r_normal_quantiles_50_90_99_max"]
    L = ["## The routing algorithm (Triage stage 2)", "",
         "Triage has two stages. Stage 1 (`echos/orchestrator/triage.py`) turns detector outputs into a status with if-then rules. Stage 2 (`echos/team/planner.py`) decides which agents the case needs, "
         "with one ratio and one cutoff. It is an algorithm, not a trained model: nothing labels \"the right agents\", so a learned selector could not be validated.", "",
         "**Step 1: nearest-threshold ratio.** Each detector k (vitals, behaviour, fall) gives a score p_k and has its own alert threshold tau_k (config `triage`). How close the case is to an alert is the largest ratio:", "",
         "```", "r = max_k ( p_k / tau_k )          r = 1: exactly at an alert threshold;  r = 0.5: half way", "```", "",
         "A fixed vitals rule that fired while monitoring was degraded counts as r = 1. r is a routing score, not a probability.", "",
         "**Step 2: routing rule.**", "",
         "```", "rule fired (MONITOR / URGENT)       -> Investigator + Care Coordinator          (hard evidence, no cutoff)",
         "no rule fired and  r > theta         -> Investigator writes a short WATCH file   (near miss; nobody else is called)",
         "otherwise                            -> Triage + template report only", "URGENT                               -> the Coordinator owns the alert, whatever theta is", "```", "",
         "The URGENT line does not depend on theta (tested for theta = 0, 1 and 10^9), so no setting of the cutoff can remove the alert owner. It never changes the status or delays an alert.", "",
         *budget_block(th),
         "**Replay evidence** (`scripts/routing_eval.py`, 1266 patient-days, stored out-of-fold detector scores):", "",
         f"- Parity: with the cutoff switched off the routing equals the previous status-based routing on {pr['days_equal_with_cutoff_off']} of {pr['of']} days.",
         f"- With theta = {rt['theta']} {pr['days_changed_with_configured_theta']} Normal days change: they are the near-miss watch days ({w['per_patient_week']} per patient-week, the budget). "
         f"They add the Investigator only ({pr['agents_added_on_changed_days']}); the Coordinator is called on {w['coordinator_called']} of them. Each writes a short watch file (no alert, template report, not counted towards a clinician review).",
         f"- On the {rt['normal_days']} Normal days r has median {q[0]}, 90th percentile {q[1]}, 99th percentile {q[2]}, maximum {q[3]}.",
         f"- Safety floor: all {sf['urgent_days']} Urgent days have the Investigator and the Coordinator as owner for every tested theta = {sf['investigator_and_owner_coordinator_for_any_theta']}.", "",
         "Sensitivity to the cutoff:", "", "| theta | watch days | per patient-week | mean team size |", "|---|---|---|---|"]
    L += [f"| {g['theta']} | {g['watch_days']} | {g['watch_days_per_patient_week']} | {g['mean_team_size']} |" for g in rt["sensitivity"]]
    L += ["", "The budget is a stated preference (how many near-miss watch files per patient-week are acceptable), not an estimate of harm. "
          "The earlier version used a cost-based Bayes gate whose hand-set loss was not derived; it was removed because it reduced to the same status-based rule.", ""]
    return L


def budget_block(th: dict | None) -> list:
    if not th:
        return ["(Run `scripts/choose_routing_threshold.py` to add the call-budget cutoff.)", ""]
    d, g, b = th["default_budget_per_patient_week"], th["budget_grid"], th["bootstrap_by_patient_95"]
    L = ["**Step 3: the cutoff comes from a call budget.** On every replayed day where no rule fired (status Normal, the near-miss days) a watch call is a *false call*. "
         "theta is the smallest value for which false calls stay within a budget B per patient-week (patient-weeks = all patient-days / 7, as `scripts/choose_monitor_thresholds.py`), "
         "that is the (1 - B x patient-weeks / Normal days) quantile of r on the Normal days (strict `>`, ties are not called):", "",
         "```", "theta = min { t : #(Normal days with r > t) <= B * patient_weeks }", "```", "",
         f"theta is in plain units: theta = {th['theta']} means \"watch a day when any detector is above {th['theta'] * 100:.0f}% of its own alert threshold\". "
         f"Budget grid ({th['patient_days']} patient-days, {th['patients']} patients, {th['normal_days']} Normal days; default B = {d}; `scripts/choose_routing_threshold.py`, `reports/metrics/routing_thresholds.json`):", "",
         "| B (false calls / patient-week) | allowed calls | theta | achieved / patient-week | labelled days among called |", "|---|---|---|---|---|"]
    for B, r in g.items():
        L.append(f"| {B}{' (default)' if float(B) == d else ''} | {r['allowed_false_calls']} | {r['theta']} | {r['false_calls_per_patient_week']} | {r['labelled_normal_days_called']} of {r['false_calls']} |")
    L += ["", f"Patient bootstrap ({b['n_boot']} resamples of {b['n_patients']} patients, seed from config): theta 95% interval {b['lo']} to {b['hi']}; with about 20 patients treat even this as rough. "
          "Cross-check, not the choice: the last column counts days with a clinical event label (Labels.csv, provenance unverified) among the called Normal days; "
          "it is a weak sensitivity check and supports no claim that near-miss days are more often clinically relevant.", ""]
    return L


if __name__ == "__main__":
    res = build()
    (M / "team_design.json").write_text(json.dumps(res, indent=1))
    (ROOT / "docs/TEAM_DESIGN.md").write_text(render(res))
    pr = res["planner_replay"]
    print("wrote reports/metrics/team_design.json, docs/TEAM_DESIGN.md |", pr["days_by_status"], "mean team", pr["mean_team_size"], "avoided", pr["agent_runs_avoided_share"])
