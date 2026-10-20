#!/usr/bin/env bash
# One command: API + watchdog + runtime (scripts/run_api.sh) and the web app (Vite), wait until both answer, print the URLs, stop everything on Ctrl-C.
#   scripts/start.sh [--replay | --live | --week-demo | --live-week] [--auth] [--fresh] [--llm] [--pubmed] [--real --yes] [--check]
#   --replay   (default) TIHM replayed day by day on a COPY of the database (data/echos_ui_dev.db, made from data/echos.db when missing)
#   --live     live / emulator mode on data/echos_live_dev.db (kept between runs: a restart recovers; add --fresh to start empty)
#   --week-demo  one person, one week (46286, Mon 2019-05-27 .. Sun 2019-06-02: low-BP critical readings + one injected fall), fresh database data/echos_week_demo.db
#              every start; press Play in Demo tools: 7 daily reports, the alerts and the weekly PDF (Sunday close) appear. Dummy dry-run addresses, nothing is sent
#   --live-week  LIVE (emulator) mode on the same person and week as --week-demo, fresh database data/echos_live_week.db: raw readings arrive one by one in wall-clock
#              time (one data day ~ 24 s, the week ~ 3.5 min); press Start in Demo tools, then 'Inject a fall now' when you want the fall
#   --real     with --week-demo or --live-week: REAL services: alert/weekly e-mails ARE sent (SMTP), Gemini and PubMed ARE called; needs --yes and the .env names
#              (README / .env.example); a preflight lists missing names (values are never printed)
#   --auth     authentication on; tokens go to data/auth_tokens.txt (git-ignored); they are NEVER printed
#   --fresh    live only: delete the live dev database first
#   --llm      allow the Gemini report agent (needs ECHOS_LLM_API_KEY in .env). Default: OFF (template reports)
#   --pubmed   allow live PubMed lookups (default OFF; --llm does not imply it)
#   --check    start, wait for the health checks, print the URLs, stop again (exit 0 = healthy); for tests
# Mail is ALWAYS a dry run here (outbox only; the SMTP mailer refuses to start). Ports: API ECHOS_API_PORT (8000), web ECHOS_WEB_PORT (5173). Logs: data/logs/ (API, web).
set -euo pipefail
cd "$(dirname "$0")/.."
MODE=replay; WEEK=0; REAL=0; YES=0; AUTH=(); FRESH=0; LLM=0; PUBMED=0; CHECK=0
for a in "$@"; do
  case "$a" in
    --replay) MODE=replay ;;
    --live) MODE=live ;;
    --week-demo) MODE=replay; WEEK=1 ;;
    --live-week) MODE=live; WEEK=2 ;;
    --auth) AUTH=(--auth) ;;
    --fresh) FRESH=1 ;;
    --llm) LLM=1 ;;
    --pubmed) PUBMED=1 ;;
    --check) CHECK=1 ;;
    --real) REAL=1 ;;
    --yes) YES=1 ;;
    -h|--help) sed -n 2,12p "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option $a (try --help)"; exit 2 ;;
  esac
done
API_PORT="${ECHOS_API_PORT:-8000}"; WEB_PORT="${ECHOS_WEB_PORT:-5173}"
API="http://127.0.0.1:$API_PORT"; WEB="http://localhost:$WEB_PORT"
for p in "$API_PORT" "$WEB_PORT"; do
  if lsof -nP -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1; then echo "port $p is already in use: stop the other process or set ECHOS_API_PORT / ECHOS_WEB_PORT"; exit 1; fi
done
[ -d web/node_modules ] || { echo "web/node_modules is missing: run 'cd web && npm ci' first"; exit 1; }
mkdir -p data/logs
if [ "$REAL" = 1 ]; then   # real-services demo: e-mail, Gemini and PubMed on; only together with --week-demo and --yes
  [ "$WEEK" != 0 ] || { echo "--real is only for --week-demo / --live-week"; exit 2; }
  [ "$YES" = 1 ] || { echo "--real sends REAL e-mails and calls Gemini / PubMed: add --yes to confirm"; exit 2; }
  PYTHONPATH="$PWD" .venv/bin/python - <<'PY' || exit 1
import os, sys
from dotenv import load_dotenv
load_dotenv(".env")
need = ["ECHOS_LLM_API_KEY", "ECHOS_NCBI_EMAIL", "ECHOS_CAREGIVER_EMAIL", "ECHOS_CLINICIAN_EMAIL", "ECHOS_SMTP_HOST", "ECHOS_SMTP_USER", "ECHOS_SMTP_PASSWORD"]
miss = [n for n in need if not os.environ.get(n)]
print("preflight: all required names set in .env" if not miss else "preflight: MISSING in .env: " + ", ".join(miss))
sys.exit(1 if miss else 0)
PY
fi
FLAGS=(--dry)                                   # never real mail
[ "$LLM" = 0 ] && FLAGS+=(--no-llm)             # --no-llm also switches PubMed off
[ "$LLM" = 1 ] && [ "$PUBMED" = 0 ] && FLAGS+=(--no-pubmed)
[ "$LLM" = 0 ] && [ "$PUBMED" = 1 ] && FLAGS=(--dry)   # PubMed only: the LLM stays off through the environment below
[ "$REAL" = 1 ] && { LLM=1; PUBMED=1; FLAGS=(); export ECHOS_SEND_REAL=1 ECHOS_WEEKLY_LLM=1; }
[ "$REAL" = 0 ] && export ECHOS_DRY_RUN=1
if [ "$REAL" = 1 ]; then   # "I have seen this" link in the mails: random secret per run, base URL = this API (it opens on this machine); both can be preset in the environment
  export ECHOS_LINK_SECRET="${ECHOS_LINK_SECRET:-$(.venv/bin/python -c 'import secrets;print(secrets.token_urlsafe(24))')}" ECHOS_PUBLIC_URL="${ECHOS_PUBLIC_URL:-$API}"
fi
[ "$LLM" = 0 ] && export ECHOS_REPORT_LLM=off
if [ "$MODE" = live ]; then
  DB=data/echos_live_dev.db
  if [ "$WEEK" = 2 ]; then
    DB=data/echos_live_week.db; FRESH=1
    export ECHOS_DEMO_PATIENTS=46286
    [ "$REAL" = 1 ] || export ECHOS_CAREGIVER_EMAIL=caregiver@example.org ECHOS_CLINICIAN_EMAIL=clinician@example.org ECHOS_ESCALATION_EMAIL=escalation@example.org   # dry run: never sent
  fi
  [ "$FRESH" = 1 ] && rm -rf "$DB" "$DB-wal" "$DB-shm" "${DB%.db}.cp.sqlite"* "${DB%.db}_files"
  FLAGS+=(--demo --mode live)
elif [ "$WEEK" = 1 ]; then
  DB=data/echos_week_demo.db
  rm -rf "$DB" "$DB-wal" "$DB-shm" "${DB%.db}.cp.sqlite"* "${DB%.db}_files"
  export ECHOS_DEMO_PATIENTS=46286 ECHOS_DEMO_START=2019-05-27 ECHOS_DEMO_FALLS=1 ECHOS_OOF=1
  [ "$REAL" = 1 ] || export ECHOS_CAREGIVER_EMAIL=caregiver@example.org ECHOS_CLINICIAN_EMAIL=clinician@example.org ECHOS_ESCALATION_EMAIL=escalation@example.org   # dry run: never sent
  FLAGS+=(--demo --patients 1 --days 7)
else
  DB=data/echos_ui_dev.db
  [ -f "$DB" ] || { [ -f data/echos.db ] || { echo "data/echos.db is missing: build the data first (README, Reproduce)"; exit 1; }; scripts/make_ui_dev_copy.sh >/dev/null; }
  FLAGS+=(--demo)
fi
set -m                                           # each background job gets its own process group, so Ctrl-C can stop the whole tree
PIDS=()
stop() {   # TERM every process group, wait up to 15 s for them to leave, then KILL what is left
  trap - EXIT INT TERM
  for p in ${PIDS[@]+"${PIDS[@]}"}; do kill -TERM -- "-$p" 2>/dev/null || true; done
  for _ in $(seq 1 30); do
    left=0; for p in ${PIDS[@]+"${PIDS[@]}"}; do kill -0 -- "-$p" 2>/dev/null && left=1; done
    [ "$left" = 0 ] && break; sleep 0.5
  done
  for p in ${PIDS[@]+"${PIDS[@]}"}; do kill -KILL -- "-$p" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap stop EXIT INT TERM
ECHOS_API_PORT="$API_PORT" ECHOS_CORS_ORIGIN="$WEB" scripts/run_api.sh "${FLAGS[@]}" ${AUTH[@]+"${AUTH[@]}"} --db "$DB" >data/logs/api.log 2>&1 &
PIDS+=($!)
(cd web && VITE_API_URL="$API" exec npx vite --host 127.0.0.1 --port "$WEB_PORT" --strictPort) >data/logs/web.log 2>&1 &
PIDS+=($!)
ok() { curl -fs -o /dev/null --max-time 3 "$1"; }
# the runtime (watchdog -> controller / live runtime) writes sim_state.updated every second or two once it is up: fresh = alive
# (a value left over from the previous run does not count: it must differ from the one seen before the start)
U0=$(sqlite3 "$DB" "select value from sim_state where key='updated'" 2>/dev/null || true)
rt_ok() { local u m; [ "$MODE" = live ] && { m=$(sqlite3 "$DB" "select value from sim_state where key='mode'" 2>/dev/null); [ -n "$m" ] || return 1; };  u=$(sqlite3 "$DB" "select value from sim_state where key='updated'" 2>/dev/null) || return 1; [ -n "$u" ] && [ "$u" != "$U0" ] && awk -v u="$u" -v n="$(date +%s)" 'BEGIN{exit !(n-u<15)}'; }
for i in $(seq 1 150); do
  ok "$API/api/auth/me" && ok "http://127.0.0.1:$WEB_PORT/" && rt_ok && break
  for p in "${PIDS[@]}"; do kill -0 "$p" 2>/dev/null || { echo "a process exited while starting: see data/logs/api.log and data/logs/web.log"; exit 1; }; done
  sleep 1
done
ok "$API/api/auth/me" && ok "http://127.0.0.1:$WEB_PORT/" && rt_ok || { echo "not healthy after 150 s: see data/logs/api.log and data/logs/web.log"; exit 1; }
if [ "$WEEK" = 2 ]; then   # load the demo week into the emulator (Mon 2019-05-27 + 8 data days: Monday 2019-06-03 only closes Sunday); Start in Demo tools plays it
  curl -fs --max-time 20 -X POST "$API/api/live/emulator" -H 'content-type: application/json' -d '{"action":"load","start_day":"2019-05-27","n_days":8}' >/dev/null || { echo "could not load the demo week into the emulator"; exit 1; }
fi
echo "ECHOS is running ($MODE mode, $([ "$REAL" = 1 ] && echo 'REAL e-mail' || echo 'dry-run mail')$([ "$LLM" = 0 ] && echo ', no LLM')$([ "$PUBMED" = 0 ] && echo ', no PubMed'))"
echo "  web: $WEB"
echo "  api: $API  (docs: $API/docs)"
[ ${#AUTH[@]} -gt 0 ] && echo "  login tokens: data/auth_tokens.txt (not printed)"
[ "$CHECK" = 1 ] && exit 0
echo "Ctrl-C stops everything."
wait "${PIDS[0]}"
