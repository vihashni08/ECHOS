# ECHOS: Evidence-Centred Health Observation and Support through Collaborative AI Agents
Multi-agent decision-support system for home-dwelling elderly patients. Not a diagnostic tool.

## Data (never cat large files; use pandas nrows or aggregate in scripts)
- data/tihm/Dataset/: Activity.csv (patient_id, location_name, date; ~1M rows, 36MB), Physiology.csv
  (patient_id, date, device_type, value, unit), Labels.csv (patient_id, date, type; 608 rows),
  Sleep.csv (only 17 patients: NOT used), Demographics.csv (patient_id, age band, sex).
- Physiology device_type values: Body Temperature, Body weight, Diastolic blood pressure, Heart rate,
  O/E - muscle mass, Skin Temperature, Systolic blood pressure, Total body water.
- Label types: Blood pressure 305, Agitation 135, Pulse 95, Body water 68, Weight 4, Body temperature 1.
- Activity rooms: Kitchen, Hallway, Lounge, Bedroom, Fridge Door, Bathroom, Front Door, Back Door.
- TIHM has NO falls and NO accelerometer. Fall model is trained on SisFall (data/sisfall/) only. Fall windows (v2, `build_windows_v2`): falls and
  ADL "peak" windows are built IDENTICALLY (centred on own max-SVM sample, +/-0.5 s jitter) plus sliding ADL windows for false-alarm exposure; v1
  (fall centred exactly on the peak, ADL sliding) leaked the peak position and is kept only for the audit (scripts/evaluation/fall_audit.py). Model: models/fall_v2.joblib.
- Date range 2019-04-01 to 2019-06-30. ~49 labelled patients: results will have wide confidence intervals.

## Architecture
Purpose: watch the sensors and vitals of one older person and tell caregiver + clinician at once about a fall / critical reading, and daily about changes from their own usual. Components are registered in echos/components.py (docs/COMPONENTS.md is generated from it; API GET /api/components; UI "How it works"):
- DETECTORS (sense, never decide): Data Quality gate (rules), Vitals + Behaviour detector (kept separate; HealthResult with SHAP, personal baseline, persistence; drift/CUSUM explanation-only), Fall detector (SisFall only, independent).
- AGENTS (goal, tools, action, trace): Triage (rule-based if-then Normal / Changed-monitor / Urgent; sequential pipeline (echos/orchestrator/pipeline.py) in replay, echos/live/processor.py in live mode, both call the same pure triage()), Case Investigator (echos/team/investigator.py: bounded tool loop, no LLM, writes the case file + trace, evidence level STRONG/MODERATE/LIMITED = not a probability), Care Coordinator (echos/team/coordinator.py + lifecycle.py: owns the alert lifecycle NEW->SENT->ACKNOWLEDGED->RESOLVED / ESCALATED / EXPIRED, escalation to ECHOS_ESCALATION_EMAIL after coordinator.ack_timeout_s, check-in and review tasks; wall-clock; state in SQLite), Report agent (LLM + tools + non-LLM validator). Agents talk over the SQLite bus (echos/team/bus.py, table agent_message).
- Infra: supervisor (replay: restarts the 3 detector workers; heartbeats in SQLite), watchdog (echos/orchestrator/watchdog.py: restarts the whole runtime process on exit / stale `sim_state.updated`; run_api.sh starts it), alert dispatcher, ingestion + live store (echos/live). Live restart: LiveProcessor.recover() (checkpoint `agent_checkpoint` 'live' + rebuild the open day from event_log); Emulator.resume() (paused at its stored position). A Sunday close builds the weekly PDF in the background.
- Auth (optional, echos/api/auth.py): `--auth` / ECHOS_AUTH=on; tokens ECHOS_TOKEN_CAREGIVER / _CLINICIAN / _OPERATOR from the environment ONLY (run_api.sh --auth writes random ones to data/auth_tokens.txt, never printed); roles caregiver < clinician < operator; devices use X-Echos-Token (ECHOS_INGEST_TOKEN). Playwright: `npx playwright test -c playwright.auth.config.ts` (API :8031, Vite :5201).
- THREE DATA MODES, labelled in the UI: REPLAY (simulator replays TIHM day by day; evaluation/demos), EMULATOR (echos/live/emulator.py: labelled device emulator replaying real TIHM raw events in wall-clock time; falls injected from SisFall), LIVE (real devices call POST /api/ingest/events; schema echos/live/schemas.py). Live detectors are scored FROM raw events (echos/live/features.py builds the patient-day row; the training table only supplies earlier history); vitals on arrival, behaviour when the day closes; an open day is PROVISIONAL (case_state.as_of / provisional). `scripts/run_api.sh --mode live` (default mode = replay). scripts/evaluation/live_parity.py checks live == replay scores.
- Triage stage 1 (echos/orchestrator/triage.py) = severity as the max over independent rules (E1) with thresholds from calibrated scores under a false-alert budget (E2/E3); equations, numbers and property checks: docs/TRIAGE_ALGORITHM.md (scripts/tuning/triage_justification.py, echos/orchestrator/triage_theory.py).
- Triage stage 2 (echos/team/planner.py): r = max_k p_k/tau_k (detector score over its own alert threshold); rule fired -> Investigator + Coordinator; no rule but r > theta (config `planner.theta`, chosen by a call budget with scripts/tuning/choose_routing_threshold.py) -> Investigator writes a short WATCH file only; URGENT always has the Coordinator as owner whatever theta is. Equations: docs/TEAM_DESIGN.md.
- NO trained fusion model: triage is plain if-then rules. `alerts.status` records only PENDING/SENT/FAILED (delivery), derived from the `outbox` table; the lifecycle (ack / escalation) lives in `alert_state` (Care Coordinator). Outbox rows: (one row per alert x recipient role: alert_id, recipient_role, address, channel, subject, body, status
  PENDING->SENT|FAILED, attempts <=3 with backoff 1,2,4 s, error, event_time, queued_time, sent_time).
- Fall and critical-vitals alerts (echos/alerts/: payload.py, dispatcher.py, mailer.py) go immediately to caregiver (ECHOS_CAREGIVER_EMAIL) and
  clinician (ECHOS_CLINICIAN_EMAIL) as a fixed template built only from structured data (no LLM, no RAG, no PubMed anywhere in that path; de-identified
  id + age band only). The orchestrator's immediate_alert node queues them BEFORE any report work; sending runs on background worker threads. Retry (alerts.retry_window_s 900, backoff 1,2,4..60 s; outbox row PENDING->RETRYING->SENT|FAILED; sweeper every 5 min for 2 h; POST /api/outbox/{id}/retry; weekly mails keep 3 attempts; state lives in the outbox table so it survives a restart). Dedup: fall
  alerts unique per event_id; critical-vitals per event plus a per-patient cooldown (alerts.critical_cooldown_sim_h). config alerts.send_real defaults
  to false = DryRunMailer (outbox only, SMTP never used). NEVER send real email from tests or demos: tests use FakeMailer only (conftest blocks
  smtplib.SMTP); scripts/realservices/send_test_email.py sends a labelled TEST mail only with --yes.
- Daily report is shown in the web app only; NORMAL days get a template report (gate: no LLM, no RAG; the gate never changes or
  delays triage or alerts). Weekly clinician report is a PDF sent by email.
- Fallbacks: health_monitoring fails -> fixed vitals thresholds + "monitoring degraded"; quality fails -> pass-through +
  "quality unchecked"; both are DATA_GAP, never NORMAL.
- Gemini limits are per minute: echos/reports/llm/llm_budget.py orders cache -> primary -> fallback model -> template, limiter 20% below each model's
  `llm.rpm`, 429 = 60 s cooldown and move on (no retry), timeouts never retried, SQLite response cache; never block alerts on the LLM.
- Daily report: patient context goes into the first prompt by code; tool loop = get_last_7_days, retrieve_guidance (max 3 tool steps); on a validator failure ONE retry request (same facts + retrieved chunks + error list, no tools, never a second tool loop); worst case 3 tools + 1 final + 1 retry = 5 requests, typical 1-2. Prompts and tool results use the alias 'Patient A' (echos/reports/llm/alias.py), never the TIHM id; mapped back in code. Reply shape = ONE pydantic model (echos/reports/daily/schema.py `DailyReport`) used for the prompt, Gemini `response_json_schema` and a strict parse (rule `schema_mismatch`). Non-LLM validator in code (errors carry rule codes; provenance has requests, retried, validator_codes). Retrieval
  (local KB first, then live PubMed via echos/knowledge/pubmed.py) only on MONITOR/URGENT days; NORMAL days = template, no LLM, no retrieval.
  Provenance (model, cache hit, tools, sources, validator, fallback reason) is stored in `reports.provenance`. Tests: FakeLLM + mocked PubMed;
  tests/conftest.py fails any outbound call to Gemini / NCBI. scripts/realservices/live_llm_check.py (real, <= 6 requests) needs --yes.
- Offline switches (echos/offline.py): ECHOS_REPORT_LLM=off, ECHOS_PUBMED=off, ECHOS_DRY_RUN=1 make GeminiLLM, the default PubMed transport and SMTPMailer refuse to start; every evaluation/demo script (e2e, run_demo, fault_injection, kill_test, alert_latency, fall_eval, gate_eval) forces them. Real-service scripts (list_models, live_llm_check, llm_smoke, make_samples, send_test_*) need --yes.
- Vitals findings are direction-aware (echos/reports/daily/vitals_why.py, echos/knowledge/relevance.py): bp_high, bp_low, hr_high, hr_low, temperature, weight_change, vitals_news2 (= readings differ in combination). Each has its own local query, PubMed terms and relevance terms; a source that does not match its finding is dropped (low BP never cites hypertension guidance; no match -> 'no supporting source retrieved'). Every vitals alert row (daily + weekly) carries a plain-words 'Why it fired' (never a probability). Weekly 'Considerations' bullets must name a concrete value/count/date and end with a citation id; the validator rejects generic ones and fewer bullets are printed rather than filler.
- Repo hygiene: .env.example lists variable NAMES only; patient-level artefacts (reports/eda/*_per_patient.csv, reports/metrics/e2e_days.json, reports/rating/) and *.db/*.joblib/*.parquet are git-ignored; secrets/addresses are masked in alert errors and API output. Reproduce: README 'Reproduce' (pinned requirements.txt, .python-version 3.11, scripts/rebuild_models.sh); tests needing built data are reported as skipped in a fresh clone (tests/conftest.py).
- Retention: scripts/dataprep/archive_events.py (echos/live/retention.py) archives old CLOSED raw `event_log` rows of a live DB to data/archive/*.jsonl.gz; dry run unless --yes; never the open day, never past the 'live' checkpoint, never alert / fall events.
- Ingest limits (echos/api/ingest_limits.py, config `ingest`, docs/INGESTION.md); ack link in alert mails (echos/alerts/acklink.py, GET /api/ack/{token}, needs ECHOS_LINK_SECRET + ECHOS_PUBLIC_URL, otherwise no link); scripts/start.sh = one-command start (dry-run mail, no LLM / PubMed unless flags).
- Docs: docs/DATA_STATEMENT.md (TIHM terms, what is/isn't published), docs/ARCHITECTURE.md, LIMITATIONS.md, DEMO.md, RESEARCH_GAPS.md (written by scripts/docgen/make_research_gaps.py from reports/metrics/*.json); scripts/docgen/check_docs_numbers.py must pass. Rating kit: scripts/docgen/make_rating_sheet.py / analyse_ratings.py. scripts/realservices/live_report_check.py (real Gemini <= 8 + PubMed <= 6, needs --yes) is never run by tests or agents without the user's say-so.
- Tests never use the real LLM (fake LLM objects only). Models train only on real data: every training table has a
  `source` column (tihm / sisfall) and trainers call echos.data.guard.assert_real_source.

## API and live runtime
- echos/api (FastAPI, `scripts/run_api.sh [--dry] [--no-llm]`): bound to 127.0.0.1, CORS for http://localhost:5173 only, authentication OPTIONAL and off by default (--auth). The API process never
  loads xgboost/torch/models: it reads SQLite + saved reports + reports/metrics/*.json and sends commands (play/pause/speed/reset, inject_fall) through the
  `sim_command` table to echos/sim/controller.py (the runtime: supervisor workers + orchestrator + controller). SSE /api/events reads `event_feed`, filled by SQLite
  triggers. Never returns raw secrets or full e-mail addresses (masked `ab***@domain`); `api.allow_kill` (default false) guards POST /api/agents/{name}/kill.
  Injected falls are synthetic SisFall windows; the response states whether the subject was in the training set of the Fall worker's model / the final model.

## Web app (web/) — pages: Today, Alerts, People (patient), Fall Monitor, Caregiver report, Clinician week, System (tabs: How it works, Agent activity, and for operators Health / Data quality / Models / Limitations; /agents and /how-it-works redirect there); mode badge on every page; Demo tools sheet (data-source controls); replay Demo guide (replay + demo mode only)
- Vite + React + TypeScript + Tailwind v4 + shadcn/ui + Recharts + React Router + TanStack Query; fonts self-hosted (@fontsource), no runtime CDN. Typed client from the
  FastAPI schema: `cd web && npm run gen:api`. Dev: backend `scripts/run_api.sh --dry --no-llm [--demo] --db data/echos_ui_dev.db` (copy made by
  `scripts/make_ui_dev_copy.sh`; never develop against data/echos.db), then `cd web && npm run dev` -> http://localhost:5173 (the API allows CORS for exactly that origin).
  Checks: `npm run build`, `npx tsc -b`, `node contrast.mjs` (WCAG AA tokens), `npx playwright test` (starts its OWN stack: API :8010 + Vite :5180 on a throw-away copy data/echos_e2e.db made from the UI dev copy; incl. axe-core checks, no serious violations allowed),
  `npx playwright test -c playwright.live.config.ts` (live-mode story: own stack API :8021 + Vite :5191, fresh data/echos_e2e_live.db), `node scripts/ui_screenshots.mjs` -> web/screenshots/. `--demo` = demo mode (agent kill + Demo guide; api.allow_kill stays false by default); `--no-llm` also switches live PubMed off (ECHOS_PUBMED=off). The Clinician page builds a missing weekly report on first visit (nothing is emailed); Send to clinician states dry run vs real email and asks twice to resend.

## Rules
- Python 3.11, pydantic, pandas, scikit-learn, xgboost, shap, fastapi, sqlalchemy/sqlite, pytest.
- Split by PATIENT (GroupKFold) for TIHM and by SUBJECT for SisFall. Never random rows.
- Personal baselines must use only past data (expanding/rolling with shift(1)): no leakage.
- Report PR-AUC, recall at fixed alert budget, false alerts per patient-week: not accuracy.
- Honest results: if something is weak, report it as weak. Save metrics to reports/metrics/*.json.
- Every agent returns the AgentResult schema in echos/schemas.py.
- Keep output short. Do not print DataFrames over 10 rows. Commit after each finished phase.

## LLM (Report agent)
- Provider: Google Gemini via the official `google-genai` Python SDK. Check the current Google docs for the exact package and call syntax before writing code; do not guess.
- API key is in the environment variable ECHOS_LLM_API_KEY (loaded from .env with python-dotenv). NEVER print, log, commit, or open .env contents.
- Model name lives in config.yaml (`llm.model`); I will set it. LLM must be optional: template mode works with no key.
- The LLM never decides emergencies; rules do.