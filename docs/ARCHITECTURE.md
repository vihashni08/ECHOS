# Architecture (ECHOS v2)

```
TIHM files ──> replay controller ──> event_log (SQLite) ─────────────────────────────┐
  (echos/sim)      day / outcome / day_close / reading                                │
                                                                                      ▼
            ┌──────────────── Patient Monitor (one per person, process) ───────────────┐
            │ belief: NIG vitals history, Beta event counts, slot history, episodes    │
            │ tools: personal_baseline, risk, agitation, alert rules                   │
            └──── urgent ───────────▶ Care agent ◀───── assign ───── Review Coordinator
                  review_request ────────────────────────────────────▶ (process)
                  ◀── feedback (day's outcome) ── Care agent (process)   lift priority + fairness, K/day
                                                    │
                                     alerts + outbox (dispatcher) ──▶ caregiver / clinician e-mail (dry run by default)
                                     review tasks, escalation

supervisor: restarts an agent process with a stale heartbeat or a stalled cursor
watchdog:   restarts the whole runtime if it dies; the controller resumes where event_log says it was
API (FastAPI): reads SQLite + reports/metrics, writes commands to sim_command; SSE from event_feed (SQLite triggers)
web (React):   Today · Review board · Patients · Alerts · Agents · How it works
```

## Design rules
- **Agents talk only through the bus** (`agent_message`). Every hand-over is a row, so the trace shown in the UI is the real communication, not a log written for show.
- **Every agent's work is idempotent and its memory persisted.**
  - The Patient Monitor writes its state per person (`patient_agent`).
  - The Coordinator writes its review history (`agent_state`).
  - The Care agent works from the outbox and `alert_state`.

  A crash at any point is recovered by a restart. The kill test compares a killed run with an uninterrupted one and finds identical alerts and review picks.
- **Order is part of correctness.** A Patient Monitor scores a person's next day only after the previous day's recorded outcome has been applied, so its history features match the offline evaluation. While a peer agent is down, the others wait (up to `review.max_wait_s`) instead of working around it.
- **URGENT never waits.**
  - The red-flag rule needs no model.
  - The `urgent` message is posted before anything else is written.
  - The Care agent handles urgent messages first in every pass.
  - The dispatcher sends on background threads with retries.
- **No in-sample demo.** The runtime scores each person with the models of the cross-validation fold that excluded them (`echos/monitor/store.py`).
- **The API never loads a model.** It reads SQLite and the metrics files, and sends commands through `sim_command`.

## Tables (echos/orchestrator/state.py)

| Table | Purpose |
|---|---|
| `event_log` | Replayed and injected events |
| `agent_output` | Patient Monitor result per event |
| `case_state` | Day status: URGENT / REVIEW / WATCH / NORMAL |
| `patient_agent` | One monitor's memory per person |
| `review_list` | Coordinator decisions and outcomes |
| `agent_message` | The bus |
| `alerts`, `outbox`, `alert_state`, `care_task` | Delivery and lifecycle |
| `agent_heartbeat`, `agent_checkpoint`, `restart_log` | Liveness and recovery |
| `sim_state`, `sim_command`, `event_feed` | API ↔ runtime and SSE |
