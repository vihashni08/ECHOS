#!/usr/bin/env bash
# Start the ECHOS runtime (3 agent workers + orchestrator + simulation controller) and the API together, for demos.
#   scripts/run_api.sh [--dry] [--no-llm] [--no-runtime] [--db PATH] [--patients N] [--days N]
#   --dry         DryRun mail: nothing is ever emailed (outbox rows only), whatever alerts.send_real says
#   --no-llm      template reports only (no Gemini calls)
#   --demo        enable POST /api/agents/{name}/kill (fault demo; default off)
#   --no-runtime  API only (read-only demo of an existing data/echos.db)
# API: http://127.0.0.1:8000  (docs at /docs). NO authentication: demo only. Stop with Ctrl-C.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
RT=1; RT_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --dry) export ECHOS_DRY_RUN=1 ;;
    --demo) export ECHOS_ALLOW_KILL=1 ;;
    --no-llm) export ECHOS_REPORT_LLM=off ;;
    --no-runtime) RT=0 ;;
    --db) export ECHOS_DB="$2"; shift ;;
    --patients|--days) RT_ARGS+=("$1" "$2"); shift ;;
    *) echo "unknown option $1"; exit 2 ;;
  esac
  shift
done
export PYTHONPATH="$PWD"
if [ -n "${ECHOS_DB:-}" ] && [ -z "${ECHOS_REPORT_ROOT:-}" ]; then   # a run on a specific DB keeps its report files next to it
  export ECHOS_REPORT_ROOT="$(dirname "$ECHOS_DB")/$(basename "${ECHOS_DB%.*}")_files"
fi
if [ "$RT" = 1 ]; then
  $PY -W ignore -m echos.sim.controller ${RT_ARGS[@]+"${RT_ARGS[@]}"} &
  RT_PID=$!
  trap 'kill $RT_PID 2>/dev/null || true' EXIT INT TERM
fi
[ "${ECHOS_DRY_RUN:-0}" = 1 ] && echo "DRY: mail goes to the outbox only"
exec $PY -m echos.api
