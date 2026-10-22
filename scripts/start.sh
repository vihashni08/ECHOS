#!/usr/bin/env bash
# One command: API + watchdog + v2 runtime (scripts/run_api.sh) and the web app (Vite), wait until both answer, print the URLs, stop everything on Ctrl-C.
#   scripts/start.sh [--keep] [--auth] [--real --yes] [--check]
#   (default)  a fresh demo database data/echos_demo.db every start: 8 TIHM patients x 28 days replayed by the three agents; press Play in Demo tools
#   --keep     keep data/echos_demo.db from the previous run instead of starting empty
#   --auth     authentication on; tokens go to data/auth_tokens.txt (git-ignored); they are NEVER printed
#   --real     REAL e-mail for URGENT alerts (SMTP from .env names, see .env.example); needs --yes; a preflight lists missing names (values never printed)
#   --check    start, wait for the health checks, print the URLs, stop again (exit 0 = healthy); for tests
# Mail is a dry run unless --real --yes (outbox only; the SMTP mailer refuses to start). Ports: API ECHOS_API_PORT (8000), web ECHOS_WEB_PORT (5173). Logs: data/logs/.
set -euo pipefail
cd "$(dirname "$0")/.."
KEEP=0; REAL=0; YES=0; AUTH=(); CHECK=0
for a in "$@"; do
  case "$a" in
    --keep) KEEP=1 ;;
    --auth) AUTH=(--auth) ;;
    --check) CHECK=1 ;;
    --real) REAL=1 ;;
    --yes) YES=1 ;;
    -h|--help) sed -n 2,9p "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option $a (try --help)"; exit 2 ;;
  esac
done
API_PORT="${ECHOS_API_PORT:-8000}"; WEB_PORT="${ECHOS_WEB_PORT:-5173}"
API="http://127.0.0.1:$API_PORT"; WEB="http://localhost:$WEB_PORT"
for p in "$API_PORT" "$WEB_PORT"; do
  if lsof -nP -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1; then echo "port $p is already in use: stop the other process or set ECHOS_API_PORT / ECHOS_WEB_PORT"; exit 1; fi
done
[ -d web/node_modules ] || { echo "web/node_modules is missing: run 'cd web && npm ci' first"; exit 1; }
[ -f models/v2/meta.json ] || { echo "models/v2 is missing: run scripts/rebuild_models.sh first"; exit 1; }
mkdir -p data/logs
if [ "$REAL" = 1 ]; then
  [ "$YES" = 1 ] || { echo "--real sends REAL e-mails: add --yes to confirm"; exit 2; }
  PYTHONPATH="$PWD" .venv/bin/python - <<'PY' || exit 1
import os, sys
from dotenv import load_dotenv
load_dotenv(".env")
need = ["ECHOS_CAREGIVER_EMAIL", "ECHOS_CLINICIAN_EMAIL", "ECHOS_SMTP_HOST", "ECHOS_SMTP_USER", "ECHOS_SMTP_PASSWORD"]
miss = [n for n in need if not os.environ.get(n)]
print("preflight: all required names set in .env" if not miss else "preflight: MISSING in .env: " + ", ".join(miss))
sys.exit(1 if miss else 0)
PY
fi
FLAGS=(--dry --demo)                            # dry-run mail unless --real
if [ "$REAL" = 1 ]; then FLAGS=(--demo); export ECHOS_SEND_REAL=1
  export ECHOS_LINK_SECRET="${ECHOS_LINK_SECRET:-$(.venv/bin/python -c 'import secrets;print(secrets.token_urlsafe(24))')}" ECHOS_PUBLIC_URL="${ECHOS_PUBLIC_URL:-$API}"
else export ECHOS_DRY_RUN=1 ECHOS_CAREGIVER_EMAIL=caregiver@example.org ECHOS_CLINICIAN_EMAIL=clinician@example.org ECHOS_ESCALATION_EMAIL=escalation@example.org   # dry run: never sent
fi
DB=data/echos_demo.db
[ "$KEEP" = 1 ] || rm -f "$DB" "$DB-wal" "$DB-shm"
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
rt_ok() { local u; u=$(sqlite3 "$DB" "select value from sim_state where key='updated'" 2>/dev/null) || return 1; [ -n "$u" ] && [ "$u" != "$U0" ] && awk -v u="$u" -v n="$(date +%s)" 'BEGIN{exit !(n-u<15)}'; }
for i in $(seq 1 150); do
  ok "$API/api/auth/me" && ok "http://127.0.0.1:$WEB_PORT/" && rt_ok && break
  for p in "${PIDS[@]}"; do kill -0 "$p" 2>/dev/null || { echo "a process exited while starting: see data/logs/api.log and data/logs/web.log"; exit 1; }; done
  sleep 1
done
ok "$API/api/auth/me" && ok "http://127.0.0.1:$WEB_PORT/" && rt_ok || { echo "not healthy after 150 s: see data/logs/api.log and data/logs/web.log"; exit 1; }
echo "ECHOS v2 is running (replay of real TIHM data, $([ "$REAL" = 1 ] && echo 'REAL e-mail' || echo 'dry-run mail'))"
echo "  web: $WEB"
echo "  api: $API  (docs: $API/docs)"
[ ${#AUTH[@]} -gt 0 ] && echo "  login tokens: data/auth_tokens.txt (not printed)"
[ "$CHECK" = 1 ] && exit 0
echo "Ctrl-C stops everything."
wait "${PIDS[0]}"
