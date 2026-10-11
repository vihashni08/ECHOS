#!/usr/bin/env bash
# Copy a database (default data/echos.db) to data/<NAME>.db (default echos_ui_dev) with its report files, so UI work and e2e tests never touch the original.
#   scripts/make_ui_dev_copy.sh [SRC_DB] [NAME]       e.g.  scripts/make_ui_dev_copy.sh data/echos_ui_dev.db echos_e2e
set -euo pipefail
cd "$(dirname "$0")/.."
SRC="${1:-data/echos.db}"; NAME="${2:-echos_ui_dev}"
DEST="data/$NAME.db"
[ "$SRC" = "$DEST" ] && { echo "source and destination are the same"; exit 2; }
rm -f "$DEST" "$DEST-wal" "$DEST-shm" "data/$NAME.cp.sqlite"
sqlite3 "$SRC" ".backup '$DEST'"
rm -rf "data/${NAME}_files" && mkdir -p "data/${NAME}_files/reports"
SRC_FILES="${SRC%.db}_files"
if [ -d "$SRC_FILES" ]; then cp -R "$SRC_FILES/." "data/${NAME}_files/"; else
  for d in daily weekly; do [ -d "reports/$d" ] && cp -R "reports/$d" "data/${NAME}_files/reports/" || true; done; fi
# stored report paths can be absolute (weekly PDFs): point them at the copy's own files directory
sqlite3 "$DEST" "UPDATE reports SET path = replace(path, '/$(basename "$SRC_FILES")/', '/${NAME}_files/') WHERE path LIKE '%/$(basename "$SRC_FILES")/%'"
echo "created $DEST; run: scripts/run_api.sh --dry --no-llm --demo --db $DEST"
