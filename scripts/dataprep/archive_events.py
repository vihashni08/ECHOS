"""Archive old, closed raw events from a LIVE database into a compressed file and delete them from event_log (see echos/live/retention.py, docs/DATA_POLICY.md).
Default is a DRY RUN (counts only). Nothing is deleted without --yes. Safe while the runtime is running (the open day, alerts and fall events are never touched),
but make a copy of the database first if it holds data you cannot rebuild.
Usage: PYTHONPATH=. .venv/bin/python scripts/dataprep/archive_events.py --db data/echos_live_dev.db --older-than-days 30 [--yes] [--include-vitals] [--out-dir data/archive] [--vacuum]"""
from __future__ import annotations

import argparse
import json
import sys

from echos.live import retention as R
from echos.orchestrator import state as S


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", required=True)
    ap.add_argument("--older-than-days", type=int, required=True)
    ap.add_argument("--out-dir", default="data/archive")
    ap.add_argument("--include-vitals", action="store_true", help="also archive vitals events (charts and vital baselines read them: usually keep)")
    ap.add_argument("--dry-run", action="store_true", help="count only (this is also the default without --yes)")
    ap.add_argument("--yes", action="store_true", help="really archive and delete")
    ap.add_argument("--vacuum", action="store_true", help="VACUUM afterwards to give the space back (locks the database while it runs)")
    a = ap.parse_args(argv)
    try:
        r = R.archive_old_events(S.get_engine(a.db), a.out_dir, a.older_than_days, dry_run=a.dry_run or not a.yes, include_vitals=a.include_vitals, vacuum=a.vacuum)
    except R.RetentionError as e:
        print(f"not archived: {e}")
        return 1
    print(json.dumps(r, indent=1))
    if r["dry_run"] and r["candidates"]:
        print("dry run: nothing was changed; add --yes to archive and delete these rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
