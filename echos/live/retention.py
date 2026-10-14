"""Data retention: move OLD, CLOSED raw events out of event_log into a compressed archive file, then delete them from the database.
Kept in the database (never archived): live_day (the closed patient-day rows every detector and chart reads), case_state, case_file, alerts, alert_state, reports, outbox, agent_output.
What is archived (default kinds): activity, heartbeat, accel. Vitals are kept unless include_vitals=True (charts and personal vital baselines read them).
A raw event is archived only when ALL of these hold:
  1. its kind is selected (never the replay kinds such as 'day');
  2. its (patient, day) is CLOSED in live_day, so the OPEN day of every patient is never touched (restart recovery rebuilds the open day from event_log);
  3. its event_id is at or below the 'live' checkpoint (agent_checkpoint): LiveProcessor.recover() only re-reads events AFTER the checkpoint, so nothing it needs again is removed;
  4. its day is older than `older_than_days` by BOTH clocks: the wall clock (`now`, injectable for tests) and the patient's own newest data day (an emulator replaying 2019 keeps its last days);
  5. no alert and no detector output (agent_output: a fall's waveform) points at the event.
The archive file is written, flushed and read back (row count + checksum) BEFORE any row is deleted; if that check fails nothing is deleted.
Consequences, also in docs/DATA_POLICY.md: archived events are no longer deduplicated (a re-sent old event is stored again and, because its day is closed, only logged as late);
raw routine charts for archived days show the original TIHM CSV rows again in emulator mode and nothing in real mode (the day-level numbers in live_day remain)."""
from __future__ import annotations

import gzip
import hashlib
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from sqlalchemy import select, text

from echos.live.store import LIVE_KINDS
from echos.orchestrator import state as S

DEFAULT_KINDS = ("activity", "heartbeat", "accel")
CHUNK = 5000


class RetentionError(Exception):
    pass


def _candidates_sql(kinds: tuple) -> str:
    ks = ",".join(f"'{k}'" for k in kinds)               # kinds come from DEFAULT_KINDS / LIVE_KINDS only (checked), never from user text
    return f"""
SELECT e.event_id FROM event_log e
JOIN live_day d ON d.patient_id = e.patient_id AND d.day = e.day AND d.closed = 1
JOIN live_patient p ON p.patient_id = e.patient_id
WHERE e.kind IN ({ks}) AND e.event_id <= :ck AND e.day < :wall_cut AND e.day < date(substr(p.last_event_ts, 1, 10), :minus)
  AND NOT EXISTS (SELECT 1 FROM alerts a WHERE a.event_id = e.event_id)
  AND NOT EXISTS (SELECT 1 FROM agent_output o WHERE o.event_id = e.event_id)
ORDER BY e.event_id"""


def archive_old_events(eng, out_dir: str | Path, older_than_days: int, *, dry_run: bool = True, include_vitals: bool = False,
                       now: Optional[Callable[[], datetime]] = None, vacuum: bool = False) -> dict:
    """-> {'dry_run', 'candidates', 'archived', 'deleted', 'by_kind', 'first_day', 'last_day', 'file', 'cutoff_wall', 'checkpoint'}; raises RetentionError."""
    if older_than_days < 1:
        raise RetentionError("older_than_days must be at least 1")
    kinds = DEFAULT_KINDS + (("vitals",) if include_vitals else ())
    assert set(kinds) <= set(LIVE_KINDS)
    with eng.begin() as c:
        ck = c.execute(select(S.agent_checkpoint.c.last_event_id).where(S.agent_checkpoint.c.agent_id == "live")).scalar()
        n_live = c.execute(text("SELECT count(*) FROM live_patient")).scalar()
    if not n_live:
        raise RetentionError("this database has no live patients (replay databases are not archived)")
    if not ck:
        raise RetentionError("no 'live' checkpoint yet: no day has closed, nothing can be archived")
    wall_cut = ((now() if now else datetime.now()).date() - timedelta(days=older_than_days)).isoformat()
    params = {"ck": int(ck), "wall_cut": wall_cut, "minus": f"-{int(older_than_days)} days"}
    with eng.begin() as c:
        ids = [r[0] for r in c.execute(text(_candidates_sql(kinds)), params)]
    out = {"dry_run": dry_run, "candidates": len(ids), "archived": 0, "deleted": 0, "by_kind": {}, "first_day": None, "last_day": None, "file": None,
           "cutoff_wall": wall_cut, "checkpoint": int(ck)}
    if not ids:
        return out
    by_kind: dict = {}
    days: list = []
    if dry_run:
        for i in range(0, len(ids), CHUNK):
            with eng.begin() as c:
                for r in c.execute(select(S.event_log.c.kind, S.event_log.c.day).where(S.event_log.c.event_id.in_(ids[i:i + CHUNK]))):
                    by_kind[r.kind] = by_kind.get(r.kind, 0) + 1
                    days.append(r.day)
        out.update(by_kind=by_kind, first_day=min(days), last_day=max(days))
        return out
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(time.time()).strftime("%Y%m%dT%H%M%S")
    path = out_dir / f"event_log_{stamp}_{ids[0]}-{ids[-1]}.jsonl.gz"
    tmp = path.with_suffix(".tmp")
    h = hashlib.sha256()
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        for i in range(0, len(ids), CHUNK):
            with eng.begin() as c:
                for r in c.execute(select(S.event_log).where(S.event_log.c.event_id.in_(ids[i:i + CHUNK])).order_by(S.event_log.c.event_id)):
                    line = json.dumps({"event_id": r.event_id, "patient_id": r.patient_id, "day": r.day, "ts": r.ts, "kind": r.kind, "payload": r.payload}, separators=(",", ":"))
                    f.write(line + "\n")
                    h.update(line.encode())
                    by_kind[r.kind] = by_kind.get(r.kind, 0) + 1
                    days.append(r.day)
    # verify by reading the finished file back BEFORE deleting anything
    n, h2 = 0, hashlib.sha256()
    with gzip.open(tmp, "rt", encoding="utf-8") as f:
        for line in f:
            h2.update(line.rstrip("\n").encode())
            n += 1
    if n != len(ids) or h2.hexdigest() != h.hexdigest():
        tmp.unlink(missing_ok=True)
        raise RetentionError(f"archive check failed ({n} of {len(ids)} rows read back); nothing was deleted")
    tmp.replace(path)
    deleted = 0
    with eng.begin() as c:
        for i in range(0, len(ids), CHUNK):
            chunk = ids[i:i + CHUNK]
            marks = ",".join(str(int(x)) for x in chunk)
            # the guards that can change since the selection are checked again in the DELETE itself
            r = c.execute(text(f"DELETE FROM event_log WHERE event_id IN ({marks}) AND NOT EXISTS (SELECT 1 FROM alerts a WHERE a.event_id = event_log.event_id) "
                               f"AND NOT EXISTS (SELECT 1 FROM agent_output o WHERE o.event_id = event_log.event_id)"))
            deleted += r.rowcount
    if vacuum:
        with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
            c.execute(text("VACUUM"))
    out.update(archived=len(ids), deleted=deleted, by_kind=by_kind, first_day=min(days), last_day=max(days), file=str(path))
    return out


def read_archive(path: str | Path):
    """Yield the archived rows (dicts) of one archive file."""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            yield json.loads(line)
