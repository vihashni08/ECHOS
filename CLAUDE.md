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
Simulator (replays TIHM day by day, injects SisFall fall windows) -> Agent 1 Physiological,
Agent 2 Behaviour, Agent 3 Fall -> LangGraph orchestrator (rule-based triage, checkpointed) ->
Agent 4 Report (LLM + tools + non-LLM validator; daily caregiver report, weekly clinician report)
-> Streamlit dashboards. A separate supervisor writes heartbeats to SQLite and restarts dead agents.
NO trained fusion model: triage is plain if-then rules (Normal / Changed-monitor / Urgent).

## Rules
- Python 3.11, pydantic, pandas, scikit-learn, xgboost, shap, langgraph, streamlit, sqlalchemy/sqlite, pytest.
- Split by PATIENT (GroupKFold) for TIHM and by SUBJECT for SisFall. Never random rows.
- Personal baselines must use only past data (expanding/rolling with shift(1)): no leakage.
- Report PR-AUC, recall at fixed alert budget, false alerts per patient-week: not accuracy.
- Honest results: if something is weak, report it as weak. Save metrics to reports/metrics/*.json.
- Every agent returns the AgentResult schema in echos/schemas.py.
- Keep output short. Do not print DataFrames over 10 rows. Commit after each finished phase.
- LLM key is ECHOS_LLM_API_KEY from .env. LLM must never decide emergencies; rules do.