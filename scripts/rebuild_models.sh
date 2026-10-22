#!/usr/bin/env bash
# Rebuild every ECHOS v2 result and model from the raw TIHM files (data/tihm/Dataset; models/ and data/v2/ are git-ignored).
# Fully offline (no e-mail). Writes reports/metrics/v2_*.json and models/v2/. Takes a few minutes. Not run by the tests.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=. ECHOS_DRY_RUN=1
PY="${PYTHON:-.venv/bin/python}"
$PY scripts/evaluation/label_audit.py            # v2_label_audit.json
$PY scripts/evaluation/vitals_eval.py            # v2_vitals.json   + data/v2/oof_vitals.parquet
$PY scripts/evaluation/agitation_eval.py         # v2_agitation.json + data/v2/oof_agitation.parquet
$PY scripts/evaluation/allocation_eval.py        # v2_allocation.json (uses the out-of-fold scores above)
$PY scripts/train_v2.py                          # models/v2/: per-fold models for the runtime + base rates
$PY scripts/evaluation/team_e2e.py               # v2_runtime.json (real agent processes, kill test)
echo "v2 results and models rebuilt"
