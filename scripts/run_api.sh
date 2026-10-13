#!/usr/bin/env bash
# Start the ECHOS runtime (3 agent workers + orchestrator + simulation controller) and the API together, for demos.
#   scripts/run_api.sh [--dry] [--no-llm] [--no-runtime] [--db PATH] [--patients N] [--days N]
#   --dry         DryRun mail: nothing is ever emailed (outbox rows only), whatever alerts.send_real says
#   --no-llm      template reports only: no Gemini calls and no live PubMed (local guideline index only)
#   --no-pubmed   no live PubMed (--no-llm implies it)
#   --demo        demo mode: enables POST /api/agents/{name}/kill and the UI's Demo guide (default off)
#   --mode live   LIVE mode: raw events -> POST /api/ingest/events -> detectors scored from events, rolling triage; with the labelled device emulator
#                 (default for `--mode live`; add --no-emulator to feed it from a real source). Default mode = replay (TIHM day by day, evaluation / demos).
#   --auth        turn authentication on: random tokens for the three roles are written to data/auth_tokens.txt (git-ignored, mode 600); ECHOS_TOKEN_* in the
#                 environment win if already set. The tokens are never printed.
#   --no-runtime  API only (read-only demo of an existing data/echos.db)
# API: http://127.0.0.1:8000  (docs at /docs; port override: ECHOS_API_PORT, CORS origin override: ECHOS_CORS_ORIGIN). NO authentication: demo only. Stop with Ctrl-C.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
RT=1; RT_ARGS=(); MODE=replay; EMU=1; AUTH=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry) export ECHOS_DRY_RUN=1 ;;
    --demo) export ECHOS_ALLOW_KILL=1 ECHOS_DEMO=1 ;;
    --no-pubmed) export ECHOS_PUBMED=off ;;
    --no-llm) export ECHOS_REPORT_LLM=off ECHOS_PUBMED=off ;;
    --mode) MODE="$2"; shift ;;
    --no-emulator) EMU=0 ;;
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
if [ -n "${ECHOS_DB:-}" ] && [ -z "${ECHOS_REPORT_ROOT:-}" ]; then   # a run on a specific DB keeps its report files next to it
  export ECHOS_REPORT_ROOT="$(cd "$(dirname "$ECHOS_DB")" && pwd)/$(basename "${ECHOS_DB%.*}")_files"
fi
PIDS=()
trap 'kill "${PIDS[@]}" 2>/dev/null || true' EXIT INT TERM
if [ "$RT" = 1 ]; then
  if [ "$MODE" = live ]; then
    LARGS=(); [ "$EMU" = 0 ] && LARGS+=(--no-emulator); [ "${ECHOS_REPORT_LLM:-}" = off ] && LARGS+=(--no-llm)
    RTCMD=($PY -W ignore -m echos.live.runtime ${LARGS[@]+"${LARGS[@]}"})
  else
    RTCMD=($PY -W ignore -m echos.sim.controller ${RT_ARGS[@]+"${RT_ARGS[@]}"})
  fi
  # the watchdog keeps the runtime process (supervisor, orchestrator / live processor, coordinator, emulator) alive: restart on exit or stale liveness
  $PY -W ignore -m echos.orchestrator.watchdog ${ECHOS_DB:+--db "$ECHOS_DB"} -- "${RTCMD[@]}" &
  PIDS+=($!)
fi
[ "${ECHOS_DRY_RUN:-0}" = 1 ] && echo "DRY: mail goes to the outbox only"
$PY -m echos.api &
PIDS+=($!)
wait "${PIDS[${#PIDS[@]}-1]}"
