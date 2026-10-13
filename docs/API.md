# ECHOS API (demo)

FastAPI app `echos.api.app:app`; interactive docs at `/docs`, schema at `/openapi.json`. **Authentication is off by default** (local demo only; `--auth` turns on three roles, see LIMITATIONS): bound to 127.0.0.1, CORS allows
`http://localhost:5173` only. The API process never imports xgboost or torch; it reads `data/echos.db`, saved reports and `reports/metrics/*.json`, and talks to the
runtime (`python -m echos.sim.controller` for replay, `python -m echos.live.runtime` for live / emulator mode) via the `sim_command` table. Server-sent events come from `event_feed` (SQLite triggers on alerts, case_state, reports,
outbox, agent_heartbeat). Lists take `limit` (1-500) and `offset`; errors look like `{"error": {"status": 404, "message": "..."}}`; e-mail addresses are masked (`ab***@domain`).

| Method | Path | Notes |
|---|---|---|
| GET | /api/patients | id, age band, sex, latest status/day, last alert, completeness |
| GET | /api/patients/{id}/summary?day= | status, plain-language reasons (never the agitation probability), data note |
| GET | /api/patients/{id}/statuses?days=7 | day, status, reasons |
| GET | /api/patients/{id}/vitals?from=&to= | series with past-only personal band (>= 5 earlier readings) and flagged points |
| GET | /api/patients/{id}/routine?from=&to= | room x hour counts; night/total activity, bathroom, transitions with bands (>= 7 earlier days) |
| GET | /api/patients/{id}/reports/daily?day= | report text + provenance ("How this report was made") |
| GET | /api/patients/{id}/reports/weekly?week= | JSON of the stored weekly report |
| GET | /api/patients/{id}/reports/weekly/pdf?week= | PDF download |
| POST | /api/patients/{id}/reports/weekly/send?week= | body `{force}`; respects `alerts.send_real`; returns the outbox rows |
| POST / GET | /api/auth/login, /api/auth/me | token -> role (caregiver / clinician / operator); only when authentication is on (`--auth`); failed logins are rate-limited. Send `Authorization: Bearer <token>`; the event stream takes `?token=` |
| GET | /api/alerts, /api/outbox | paginated; alerts carry the lifecycle state (NEW / SENT / ACKNOWLEDGED / RESOLVED / ESCALATED / EXPIRED), deadline and acknowledgement |
| POST | /api/alerts/{id}/ack, /resolve | acknowledge (stops the escalation timer) / mark as handled; idempotent |
| GET | /api/tasks?status=OPEN, POST /api/tasks/{id}/done | care tasks opened by the Care Coordinator (check-in after a fall, clinician review after repeated changes) |
| GET | /api/patients/{id}/case?day= | the Case Investigator's file (headline, evidence level, next checks, caveats, trace); `null` when that day has none |
| GET | /api/components, /api/agents/messages | the detector / agent / infrastructure registry with live status; the message bus |
| POST | /api/ingest/events | ingestion contract for any source (activity, vitals, accel, heartbeat; data timestamps; idempotent); optional `X-Echos-Token` when `ECHOS_INGEST_TOKEN` is set |
| GET | /api/live/status | data mode (replay / emulator / live), emulator state, per-patient stream freshness |
| POST | /api/live/emulator | `{action: load / play / pause / speed / fall}`: control the labelled device emulator |
| GET | /api/falls, /api/falls/{id} | detail: downsampled 3-axis waveform, impact, probability, inactivity, alert + outbox rows |
| POST | /api/sim/fall | inject a SisFall fall window (synthetic); states the subject and training-set membership |
| GET / POST | /api/sim/state, /api/sim/control | play / pause / speed / reset |
| GET | /api/events | SSE; heartbeat comment every 15 s; reconnect with Last-Event-ID |
| GET | /api/agents/health | heartbeat age and status |
| POST | /api/agents/{name}/kill | only when `api.allow_kill: true` |
| GET | /api/system/quality, /metrics, /llm-budget, /limitations | metrics are read from the JSON files |

An injected fall closes the patient's current simulated day at once, so the alert is raised now rather than at 23:59:59.
