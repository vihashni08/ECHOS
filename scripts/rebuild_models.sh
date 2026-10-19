#!/usr/bin/env bash
# Rebuild every table and model from the raw data (models/ and data/processed/ are git-ignored). Needs data/tihm/Dataset and data/sisfall/SisFall_dataset
# in place (see README). Fully offline: no Gemini, PubMed or e-mail. Takes several minutes (SisFall windowing and the nested cross-validation dominate).
# Not run by the tests. Writes reports/metrics/{agent1,agent2,agent3,calibration,monitor_thresholds,table_rebuild}.json.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=. ECHOS_REPORT_LLM=off ECHOS_PUBMED=off ECHOS_DRY_RUN=1
PY="${PYTHON:-python}"
$PY scripts/dataprep/build_tables.py                       # data/processed/patient_day.parquet + sisfall_windows_v2.parquet
$PY -m echos.agents.health.vitals                 # models/physiological.joblib   (+ reports/metrics/agent1.json)
$PY -m echos.agents.health.behaviour              # models/behaviour.joblib       (+ agent2.json)
$PY -m echos.agents.fall                          # models/fall_v2.joblib         (+ agent3.json)
$PY scripts/tuning/calibrate.py                          # models/*_calibrated.joblib
$PY scripts/tuning/choose_monitor_thresholds.py          # models/oof_scores.parquet (out-of-fold scores used by the replay / e2e)
$PY -c "from echos.sim.replay import train_replay_fall_model as t; t(force=True)"   # models/fall_replay_v2.joblib (held-out SisFall subjects excluded)
echo "models rebuilt"
