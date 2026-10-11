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
  (fall centred exactly on the peak, ADL sliding) leaked the peak position and is kept only for the audit (scripts/fall_audit.py). Model: models/fall_v2.joblib.
- Date range 2019-04-01 to 2019-06-30. ~49 labelled patients: results will have wide confidence intervals.

## Architecture
Simulator (replays TIHM day by day, injects SisFall fall windows; synthetic, tests/demos only) -> Data Quality agent
(rule-based) -> Health Monitoring agent (vitals + behaviour sub-models, kept separate; returns HealthResult with SHAP,
personal-baseline and persistence evidence; drift/CUSUM is explanation-only) and Fall agent (SisFall only, independent
of both) -> LangGraph orchestrator (rule-based triage Normal / Changed-monitor / Urgent, checkpointed) -> Report agent
(LLM + tools + non-LLM validator) -> Streamlit dashboards. A supervisor runs three workers (quality, health_monitoring,
fall), writes heartbeats to SQLite and restarts dead ones.
- NO trained fusion model: triage is plain if-then rules. NO alert lifecycle (no ack/escalation): `alerts` rows record only PENDING/SENT/FAILED,
  derived from the `outbox` table (one row per alert x recipient role: alert_id, recipient_role, address, channel, subject, body, status
  PENDING->SENT|FAILED, attempts <=3 with backoff 1,2,4 s, error, event_time, queued_time, sent_time).
- Fall and critical-vitals alerts (echos/alerts/: payload.py, dispatcher.py, mailer.py) go immediately to caregiver (ECHOS_CAREGIVER_EMAIL) and
  clinician (ECHOS_CLINICIAN_EMAIL) as a fixed template built only from structured data (no LLM, no RAG, no PubMed anywhere in that path; de-identified
  id + age band only). The orchestrator's immediate_alert node queues them BEFORE any report work; sending runs on background worker threads. Dedup: fall
  alerts unique per event_id; critical-vitals per event plus a per-patient cooldown (alerts.critical_cooldown_sim_h). config alerts.send_real defaults
  to false = DryRunMailer (outbox only, SMTP never used). NEVER send real email from tests or demos: tests use FakeMailer only (conftest blocks
  smtplib.SMTP); scripts/send_test_email.py sends a labelled TEST mail only with --yes.
- Daily report is dashboard-only; NORMAL days get a template report (gate: no LLM, no RAG; the gate never changes or
  delays triage or alerts). Weekly clinician report is a PDF sent by email.
- Fallbacks: health_monitoring fails -> fixed vitals thresholds + "monitoring degraded"; quality fails -> pass-through +
  "quality unchecked"; both are DATA_GAP, never NORMAL.
- Gemini limits are per minute: echos/reports/llm_budget.py orders cache -> primary -> fallback model -> template, limiter 20% below each model's
  `llm.rpm`, 429 = 60 s cooldown and move on (no retry), timeouts never retried, SQLite response cache; never block alerts on the LLM.
- Daily report = tool loop (get_patient_context, get_last_7_days, retrieve_guidance; max 4 tool steps) + non-LLM validator in code. Retrieval
  (local KB first, then live PubMed via echos/knowledge/pubmed.py) only on MONITOR/URGENT days; NORMAL days = template, no LLM, no retrieval.
  Provenance (model, cache hit, tools, sources, validator, fallback reason) is stored in `reports.provenance`. Tests: FakeLLM + mocked PubMed;
  tests/conftest.py fails any outbound call to Gemini / NCBI. scripts/live_llm_check.py (real, <= 6 requests) needs --yes.
- Tests never use the real LLM (fake LLM objects only). Models train only on real data: every training table has a
  `source` column (tihm / sisfall) and trainers call echos.data.guard.assert_real_source.

## API and live runtime
- echos/api (FastAPI, `scripts/run_api.sh [--dry] [--no-llm]`): bound to 127.0.0.1, CORS for http://localhost:5173 only, NO authentication (demo). The API process never
  loads xgboost/torch/models: it reads SQLite + saved reports + reports/metrics/*.json and sends commands (play/pause/speed/reset, inject_fall) through the
  `sim_command` table to echos/sim/controller.py (the runtime: supervisor workers + orchestrator + controller). SSE /api/events reads `event_feed`, filled by SQLite
  triggers. Never returns raw secrets or full e-mail addresses (masked `ab***@domain`); `api.allow_kill` (default false) guards POST /api/agents/{name}/kill.
  Injected falls are synthetic SisFall windows; the response states whether the subject was in the training set of the Fall worker's model / the final model.

## Rules
- Python 3.11, pydantic, pandas, scikit-learn, xgboost, shap, langgraph, streamlit, sqlalchemy/sqlite, pytest.
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