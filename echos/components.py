"""Single registry of every ECHOS v2 component: what it is, what it owns, what it consumes / emits, how it fails. Served by GET /api/components
(the UI's "How it works"). kind: agent (own goal, belief and decisions; a separate process), tool (called by an agent, decides nothing),
infra (keeps things running)."""
from __future__ import annotations

COMPONENTS = [
    {"id": "patient_monitor", "name": "Patient Monitor", "kind": "agent", "worker": True,
     "role": "One per person. Learns what is usual for this person (blood pressure, pulse, daily activity by time of day), scores each day, flags probable sensor silence or stale vitals, "
             "sends an URGENT message on a red-flag reading and a review request with plain-words reasons after the day's feedback.",
     "inputs": "the day's readings and activity windows; FEEDBACK messages (clinician / study outcome)",
     "outputs": "urgent -> Care agent; review_request (vitals risk, agitation risk, reasons) -> Review Coordinator",
     "on_failure": "the supervisor restarts it; it resumes from its saved memory and checkpoint, and nothing is scored twice",
     "never": "send email itself, or decide who is reviewed"},
    {"id": "review_coordinator", "name": "Review Coordinator", "kind": "agent", "worker": True,
     "role": "Shares the clinical team's K daily reviews across everyone: priority = how far each person's risk is above its own base rate, "
             "with a fairness floor so nobody goes unseen for more than a week.",
     "inputs": "review_request messages; the day's close",
     "outputs": "the daily review list; assign -> Care agent; review_decision -> Patient Monitors",
     "on_failure": "the supervisor restarts it; while a monitor is down it waits instead of allocating with missing requests",
     "never": "downgrade an URGENT case or send anything itself"},
    {"id": "care", "name": "Care agent", "kind": "agent", "worker": True,
     "role": "Makes sure every urgent case and every assigned review reaches a person: queues the fixed-template alert at once, tracks "
             "acknowledgement, escalates when nobody answers, opens review tasks, and passes the clinician's outcome back as FEEDBACK.",
     "inputs": "urgent and assign messages; clinician outcomes",
     "outputs": "alert e-mails (dry run unless configured), review tasks, feedback -> Patient Monitor",
     "on_failure": "the outbox keeps every alert; a restarted Care agent resumes sending and escalating from the database",
     "never": "change a risk score or a review decision"},
    {"id": "evidence", "name": "Evidence agent", "kind": "agent", "worker": True,
     "role": "Gives every urgent alert and every assigned review a short explanation that cites guideline passages (NICE NG136, RCP NEWS2) found by a local index; "
             "Gemini writes the wording from the retrieved passages only, a non-LLM validator checks it, and a fixed template is used when anything fails.",
     "inputs": "evidence_request messages from the Care agent (sent after the alert is queued)",
     "outputs": "a cited evidence card per case (evidence_card table); evidence_ready broadcast",
     "on_failure": "the supervisor restarts it; answers are cached so nothing is asked twice; with no key, no network or a failed check the template card is used",
     "never": "delay an URGENT alert, change a risk or a review decision, or give a diagnosis or medication advice"},
    {"id": "personal_baseline", "name": "Personal baseline (tool)", "kind": "tool", "worker": False,
     "role": "Normal-Inverse-Gamma model of each person's usual reading: 'systolic 172 vs usual 138 (+3.1 SD)'.",
     "inputs": "the person's past readings", "outputs": "usual, z-score, surprise", "on_failure": "a new person starts at the population usual",
     "never": "use future readings"},
    {"id": "event_rate", "name": "Personal event rate (tool)", "kind": "tool", "worker": False,
     "role": "Beta-Bernoulli rate of confirmed BP / pulse events with an empirical-Bayes prior: the vitals review priority, with a 95% credible interval.",
     "inputs": "confirmed outcomes so far", "outputs": "3-day risk", "on_failure": "falls back to the pooled rate", "never": "claim to forecast a new episode"},
    {"id": "agitation_detector", "name": "Agitation detector (tool)", "kind": "tool", "worker": False,
     "role": "Gradient-boosted trees on room / door sensor windows against the person's usual for that time of day, calibrated.",
     "inputs": "activity window features + personal same-slot history", "outputs": "probability + the features furthest above usual",
     "on_failure": "a window with no history uses raw counts only", "never": "trigger an urgent alert"},
    {"id": "alert_rules", "name": "Alert rules (tool)", "kind": "tool", "worker": False,
     "role": "URGENT = NEWS2 red scores or BP >= 180/120 (no model involved). TIHM's own daily-check limits are attention notes only.",
     "inputs": "one reading", "outputs": "reasons", "on_failure": "missing values never fire", "never": "use a learned model"},
    {"id": "bus", "name": "Message bus", "kind": "infra", "worker": False,
     "role": "SQLite table agent_message: the only channel between the agents (who told whom what, and when).",
     "inputs": "post()", "outputs": "poll() / done()", "on_failure": "unconsumed messages wait; nothing is lost on restart", "never": "drop an unhandled message"},
    {"id": "supervisor", "name": "Supervisor + watchdog", "kind": "infra", "worker": False,
     "role": "Restarts an agent process whose heartbeat stops or whose cursor stalls; the watchdog restarts the whole runtime if it dies.",
     "inputs": "heartbeats", "outputs": "restarts (restart_log)", "on_failure": "the API reports 'runtime not running'", "never": "change data"},
    {"id": "replay", "name": "Replay controller", "kind": "infra", "worker": False,
     "role": "Replays real TIHM days (readings, activity windows, then the study's confirmed outcomes) for the demo cohort; "
             "can inject one reading for a demo.",
     "inputs": "TIHM files", "outputs": "event_log rows", "on_failure": "pauses; state kept in sim_state", "never": "invent data (only the injected demo reading)"},
]
