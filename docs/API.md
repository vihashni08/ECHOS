# ECHOS API (demo)

FastAPI app `echos.api.app:app`; interactive docs at `/docs`, schema at `/openapi.json`. **No authentication** (local demo only): bound to 127.0.0.1, CORS allows
`http://localhost:5173` only. The API process never imports xgboost or torch; it reads `data/echos.db`, saved reports and `reports/metrics/*.json`, and talks to the
runtime (`python -m echos.sim.controller`) via the `sim_command` table. Server-sent events come from `event_feed` (SQLite triggers on alerts, case_state, reports,
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
| GET | /api/alerts, /api/outbox | paginated |
| GET | /api/falls, /api/falls/{id} | detail: downsampled 3-axis waveform, impact, probability, inactivity, alert + outbox rows |
| POST | /api/sim/fall | inject a SisFall fall window (synthetic); states the subject and training-set membership |
| GET / POST | /api/sim/state, /api/sim/control | play / pause / speed / reset |
| GET | /api/events | SSE; heartbeat comment every 15 s; reconnect with Last-Event-ID |
| GET | /api/agents/health | heartbeat age and status |
| POST | /api/agents/{name}/kill | only when `api.allow_kill: true` |
| GET | /api/system/quality, /metrics, /llm-budget, /limitations | metrics are read from the JSON files |

An injected fall closes the patient's current simulated day at once, so the alert is raised now rather than at 23:59:59.
