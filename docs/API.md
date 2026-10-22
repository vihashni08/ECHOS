# ECHOS API

FastAPI app `echos.api.app:app`. Interactive docs are at `/docs` and the schema at `/openapi.json` (the web client is generated from it: `cd web && npm run gen:api`).

**Bound to 127.0.0.1**, with CORS for `http://localhost:5173` only. **Authentication is optional** (`--auth`), with three roles: caregiver < clinician < operator (`echos/api/auth.py`).

The API never loads a model. It reads SQLite and `reports/metrics/*.json`, and talks to the runtime through the `sim_command` table.

**Conventions:**
- Lists take `limit` (1–500) and `offset`.
- Errors look like `{"error": {"status": 404, "message": "..."}}`.
- E-mail addresses are masked.

| Method | Path | Role | Notes |
|---|---|---|---|
| GET | /api/v2/review?day= | caregiver | Review Coordinator's list (latest day by default): rank, reason, priority, risks, lifts, plain-words why, outcome; deferred patients; allocated days |
| GET | /api/v2/patients/{id}/monitor?limit= | caregiver | Patient Monitor's days, newest first: readings vs personal usual, urgent / attention flags, agitation windows, event rate + 95% CI, review rank |
| POST | /api/v2/reviews/outcome | clinician | Clinician's answer `{patient_id, day, vitals_event, agitation_windows}` → Care agent → Patient Monitor feedback |
| GET | /api/v2/summary | caregiver | Counts for the dashboard |
| GET | /api/patients | caregiver | Id, age band, sex, latest day / status, last alert, days scored, reviews |
| GET | /api/patients/{id}/statuses?days= | caregiver | Day statuses with reasons |
| GET | /api/alerts | caregiver | Alerts with their lifecycle state (NEW / SENT / ACKNOWLEDGED / RESOLVED / ESCALATED / EXPIRED) |
| POST | /api/alerts/{id}/ack, /resolve | caregiver | Stops the escalation timer / closes the alert |
| GET | /api/ack/{token} | public | Signed, expiring "I have seen this" link from the alert e-mail |
| GET | /api/tasks, POST /api/tasks/{id}/done | caregiver | Care tasks (review assignments) |
| GET | /api/outbox, POST /api/outbox/{id}/retry | clinician | Delivery log; resend one row |
| GET | /api/agents/messages?patient_id= | caregiver | The bus: who told whom what |
| GET | /api/components | caregiver | Agents, tools, infrastructure with live status |
| GET | /api/agents/health | clinician | Heartbeats, restarts |
| POST | /api/agents/{name}/kill | operator | Demo fault (needs `api.allow_kill` / `--demo`) |
| GET | /api/sim/state, POST /api/sim/control | caregiver / operator | Replay state; play / pause / speed / reset |
| POST | /api/sim/reading | operator | Demo: inject one reading for a replay patient now |
| GET | /api/events | caregiver | Server-sent events: alert, day_status, review_list, agent_message, care_task, outbox_status, alert_state, agent_health |
| GET | /api/system/metrics, /limitations, /config | caregiver | Evaluation results, limitations text, UI flags |
| POST | /api/auth/login, GET /api/auth/me | public | Optional authentication |
