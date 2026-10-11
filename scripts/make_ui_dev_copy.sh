#!/usr/bin/env bash
# Copy data/echos.db to data/echos_ui_dev.db (+ its report files) so UI work never touches the original. Usage: scripts/make_ui_dev_copy.sh
set -euo pipefail
cd "$(dirname "$0")/.."
rm -f data/echos_ui_dev.db data/echos_ui_dev.db-wal data/echos_ui_dev.db-shm data/echos_ui_dev.cp.sqlite
sqlite3 data/echos.db ".backup 'data/echos_ui_dev.db'"
rm -rf data/echos_ui_dev_files && mkdir -p data/echos_ui_dev_files/reports
for d in daily weekly; do [ -d reports/$d ] && cp -R reports/$d data/echos_ui_dev_files/reports/ || true; done
echo "created data/echos_ui_dev.db; run: scripts/run_api.sh --dry --no-llm --demo --db data/echos_ui_dev.db"
