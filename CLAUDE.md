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
- TIHM has NO falls and NO accelerometer. Fall model is trained on SisFall (data/sisfall/) only.
- Date range 2019-04-01 to 2019-06-30. ~49 labelled patients: results will have wide confidence intervals.

## Architecture
Simulator (replays TIHM day by day, injects SisFall fall windows; synthetic, tests/demos only) -> Data Quality agent
(rule-based) -> Health Monitoring agent (vitals + behaviour sub-models, kept separate; returns HealthResult with SHAP,
personal-baseline and persistence evidence; drift/CUSUM is explanation-only) and Fall agent (SisFall only, independent
of both) -> LangGraph orchestrator (rule-based triage Normal / Changed-monitor / Urgent, checkpointed) -> Report agent
(LLM + tools + non-LLM validator) -> Streamlit dashboards. A supervisor runs three workers (quality, health_monitoring,
fall), writes heartbeats to SQLite and restarts dead ones.
- NO trained fusion model: triage is plain if-then rules. NO alert lifecycle: `alerts` rows are alert_id, patient_id,
  type, time, payload, status 'SENT'.
- Fall and critical-vitals alerts go immediately to caregiver and clinician as a fixed template (no LLM).
- Daily report is dashboard-only; NORMAL days get a template report (gate: no LLM, no RAG; the gate never changes or
  delays triage or alerts). Weekly clinician report is a PDF sent by email.
- Fallbacks: health_monitoring fails -> fixed vitals thresholds + "monitoring degraded"; quality fails -> pass-through +
  "quality unchecked"; both are DATA_GAP, never NORMAL.
- Gemini limits are per minute: budget requests (llm.max_requests_per_min), cache, never block alerts on the LLM.
- Tests never use the real LLM (fake LLM objects only). Models train only on real data: every training table has a
  `source` column (tihm / sisfall) and trainers call echos.data.guard.assert_real_source.

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