#!/usr/bin/env bash
# Start the ECHOS v2 runtime (replay controller + Patient Monitor, Review Coordinator and Care agent processes under a supervisor, kept alive by the
# watchdog) and the API together.
#   scripts/run_api.sh [--dry] [--demo] [--auth] [--no-runtime] [--db PATH] [--patients N] [--days N]
#   --dry         DryRun mail: nothing is ever e-mailed (outbox rows only), whatever alerts.send_real says
#   --demo        demo mode: enables POST /api/agents/{name}/kill (default off)
#   --auth        authentication on: random tokens for the three roles are written to data/auth_tokens.txt (git-ignored, mode 600); ECHOS_TOKEN_* in the
#                 environment win if already set. The tokens are never printed.
#   --no-runtime  API only (read-only view of an existing database)
# API: http://127.0.0.1:8000 (docs at /docs; port override ECHOS_API_PORT, CORS origin override ECHOS_CORS_ORIGIN). Stop with Ctrl-C.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-.venv/bin/python}"
RT=1; RT_ARGS=(); AUTH=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry) export ECHOS_DRY_RUN=1 ;;
    --demo) export ECHOS_ALLOW_KILL=1 ECHOS_DEMO=1 ;;
    --auth) AUTH=1 ;;
    --no-runtime) RT=0 ;;
    --db) export ECHOS_DB="$2"; shift ;;
    --patients|--days) RT_ARGS+=("$1" "$2"); shift ;;
    *) echo "unknown option $1"; exit 2 ;;
  esac
  shift
done
export PYTHONPATH="$PWD"
if [ "$AUTH" = 1 ]; then
  export ECHOS_AUTH=on
  TF=data/auth_tokens.txt; umask 077; mkdir -p data
  for R in CAREGIVER CLINICIAN OPERATOR; do
    V="ECHOS_TOKEN_$R"
    if [ -z "${!V:-}" ]; then export "$V=$($PY -c 'import secrets;print(secrets.token_urlsafe(18))')"; fi
  done
  printf 'caregiver %s\nclinician %s\noperator %s\n' "$ECHOS_TOKEN_CAREGIVER" "$ECHOS_TOKEN_CLINICIAN" "$ECHOS_TOKEN_OPERATOR" > "$TF"
  echo "AUTH on: tokens for the three roles are in $TF (not printed)"
fi
PIDS=()
trap 'kill "${PIDS[@]}" 2>/dev/null || true' EXIT INT TERM
if [ "$RT" = 1 ]; then
  [ -f models/v2/meta.json ] || { echo "models/v2 is missing: run scripts/rebuild_models.sh (or PYTHONPATH=. python scripts/train_v2.py)"; exit 1; }
  # the watchdog keeps the runtime process alive: restart on exit or stale liveness (sim_state.updated)
  $PY -W ignore -m echos.orchestrator.watchdog ${ECHOS_DB:+--db "$ECHOS_DB"} -- $PY -W ignore -m echos.team.runtime ${RT_ARGS[@]+"${RT_ARGS[@]}"} &
  PIDS+=($!)
fi
[ "${ECHOS_DRY_RUN:-0}" = 1 ] && echo "DRY: mail goes to the outbox only"
$PY -m echos.api &
PIDS+=($!)
wait "${PIDS[${#PIDS[@]}-1]}"
