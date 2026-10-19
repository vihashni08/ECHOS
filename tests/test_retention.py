"""Archiving old raw events: guards (open day, checkpoint, alert / fall events, vitals), dry run, fake clock, verified archive, restart recovery. No network."""
import gzip
import json
from datetime import datetime

import pytest
from sqlalchemy import func, select

from echos.live import retention as R
from echos.orchestrator import state as S
from scripts.dataprep import archive_events as CLI

from tests.test_live import build, run_emulator, eng as live_eng  # noqa: F401  (fixture reused for the integration test)
from echos.live.emulator import Emulator


@pytest.fixture
def db(tmp_path):
    return S.get_engine(tmp_path / "r.db")


def add(c, pid, day, kind, i=0, **pl):
    payload = {"device_id": "d1", "source": "device", **({"room": "Kitchen"} if kind == "activity" else {}), **pl}
    return c.execute(S.event_log.insert().values(patient_id=pid, day=day, ts=f"{day}T08:{i:02d}:00", kind=kind, payload=json.dumps(payload))).inserted_primary_key[0]


def seed(eng):
    """Patient A: days 05-01..05-04 closed, 05-05 open (newest data day 05-05). Patient B: 05-01 closed but its events come AFTER the checkpoint."""
    ids = {}
    with eng.begin() as c:
        for pid in ("A", "B"):
            S.upsert(c, S.live_patient, ["patient_id"], patient_id=pid, source="device", first_wall=1.0, last_event_ts="2019-05-05T09:00:00")
        for d in ("2019-05-01", "2019-05-02", "2019-05-03", "2019-05-04"):
            for k in ("activity", "vitals", "heartbeat"):
                ids[("A", d, k)] = add(c, "A", d, k, device_type="Heart rate", value=70.0) if k == "vitals" else add(c, "A", d, k)
            S.upsert(c, S.live_day, ["patient_id", "day"], patient_id="A", day=d, row="{}", closed=True, updated_ts=1.0)
        for k in ("activity", "vitals", "heartbeat"):
            ids[("A", "2019-05-05", k)] = add(c, "A", "2019-05-05", k, device_type="Heart rate", value=70.0) if k == "vitals" else add(c, "A", "2019-05-05", k)
        S.upsert(c, S.live_day, ["patient_id", "day"], patient_id="A", day="2019-05-05", row="{}", closed=False, updated_ts=1.0)
        ids["fall"] = add(c, "A", "2019-05-02", "accel", i=30, fs=100, samples=[[0, 0, 1]])          # a fall: detector output points at it
        ids["alerted"] = add(c, "A", "2019-05-03", "accel", i=31, fs=100, samples=[[0, 0, 1]])        # an alert points at it
        ids["plain_accel"] = add(c, "A", "2019-05-01", "accel", i=32, fs=100, samples=[[0, 0, 1]])
        c.execute(S.agent_output.insert().values(event_id=ids["fall"], agent_id="fall", patient_id="A", day="2019-05-02", result="{}", done_ts=1.0))
        c.execute(S.alerts.insert().values(alert_id="A|2019-05-03|fall|x", patient_id="A", type="fall", time=1.0, payload="{}", status="SENT", event_id=ids["alerted"], event_time="2019-05-03T08:31:00"))
        ck = max(ids.values())
        S.upsert(c, S.agent_checkpoint, ["agent_id"], agent_id="live", last_event_id=ck, ts=1.0)
        ids[("B", "2019-05-01", "activity")] = add(c, "B", "2019-05-01", "activity")                    # id > checkpoint
        S.upsert(c, S.live_day, ["patient_id", "day"], patient_id="B", day="2019-05-01", row="{}", closed=True, updated_ts=1.0)
    return ids


def remaining(eng):
    with eng.begin() as c:
        return {r[0] for r in c.execute(select(S.event_log.c.event_id))}


NOW = lambda: datetime(2026, 1, 1)  # noqa: E731


def test_dry_run_changes_nothing_and_counts(db, tmp_path):
    ids = seed(db)
    before = remaining(db)
    r = R.archive_old_events(db, tmp_path / "arch", 1, dry_run=True, now=NOW)
    assert r["dry_run"] and r["candidates"] > 0 and r["deleted"] == 0 and r["file"] is None and remaining(db) == before
    assert not (tmp_path / "arch").exists()
    assert set(r["by_kind"]) == {"activity", "heartbeat", "accel"} and r["first_day"] == "2019-05-01" and r["last_day"] == "2019-05-03"      # strictly older than 1 day before the newest data day 05-05: days up to 05-03


def test_only_old_closed_unreferenced_events_up_to_the_checkpoint_are_archived(db, tmp_path):
    ids = seed(db)
    r = R.archive_old_events(db, tmp_path / "arch", 1, dry_run=False, now=NOW)
    left = remaining(db)
    assert ids[("A", "2019-05-04", "activity")] in left                                  # 05-04 is only 1 day older than the newest data day: kept
    # the open day is untouched
    assert all(ids[("A", "2019-05-05", k)] in left for k in ("activity", "vitals", "heartbeat"))
    # vitals are kept by default
    assert all(ids[("A", d, "vitals")] in left for d in ("2019-05-01", "2019-05-02", "2019-05-03", "2019-05-04"))
    # fall / alert events, and events after the checkpoint, stay
    assert ids["fall"] in left and ids["alerted"] in left and ids[("B", "2019-05-01", "activity")] in left
    # the plain old closed activity / heartbeat / accel rows are gone
    gone = [ids[("A", d, k)] for d in ("2019-05-01", "2019-05-02", "2019-05-03") for k in ("activity", "heartbeat")] + [ids["plain_accel"]]
    assert not (set(gone) & left) and r["deleted"] == r["archived"] == len(gone)


def test_older_than_days_uses_the_patients_own_clock_too(db, tmp_path):
    seed(db)
    r = R.archive_old_events(db, tmp_path / "a1", 3, dry_run=False, now=NOW)           # newest data day 05-05 -> only days before 05-02 count
    assert r["last_day"] == "2019-05-01"
    with db.begin() as c:
        assert c.execute(select(func.count()).select_from(S.event_log).where(S.event_log.c.day == "2019-05-02").where(S.event_log.c.kind == "activity")).scalar() == 1


def test_wall_clock_guard_with_a_fake_clock(db, tmp_path):
    seed(db)
    early = lambda: datetime(2019, 5, 2)  # noqa: E731    # the wall clock says 05-02: nothing is more than 1 day old yet, whatever the data says
    r = R.archive_old_events(db, tmp_path / "a", 1, dry_run=False, now=early)
    assert r["archived"] == 0 and r["candidates"] == 0 and r["cutoff_wall"] == "2019-05-01"
    r2 = R.archive_old_events(db, tmp_path / "a", 1, dry_run=False, now=lambda: datetime(2019, 5, 3))
    assert r2["last_day"] == "2019-05-01" and r2["cutoff_wall"] == "2019-05-02"


def test_include_vitals_archives_old_vitals_but_not_the_open_day(db, tmp_path):
    ids = seed(db)
    R.archive_old_events(db, tmp_path / "a", 1, dry_run=False, include_vitals=True, now=NOW)
    left = remaining(db)
    assert not any(ids[("A", d, "vitals")] in left for d in ("2019-05-01", "2019-05-03")) and ids[("A", "2019-05-04", "vitals")] in left and ids[("A", "2019-05-05", "vitals")] in left


def test_archive_file_round_trips_and_is_checked_before_delete(db, tmp_path):
    seed(db)
    r = R.archive_old_events(db, tmp_path / "arch", 1, dry_run=False, now=NOW)
    rows = list(R.read_archive(r["file"]))
    assert len(rows) == r["archived"] and rows == sorted(rows, key=lambda x: x["event_id"])
    assert all(set(x) == {"event_id", "patient_id", "day", "ts", "kind", "payload"} and json.loads(x["payload"])["device_id"] == "d1" for x in rows)


def test_failed_verification_deletes_nothing(db, tmp_path, monkeypatch):
    seed(db)
    before = remaining(db)
    real = gzip.open

    def lossy(path, mode="rb", *a, **k):
        f = real(path, mode, *a, **k)
        if "r" in mode:
            lines = f.readlines()[:-1]            # the read-back loses one row
            f.close()
            import io
            return io.StringIO("".join(lines))
        return f
    monkeypatch.setattr(R.gzip, "open", lossy)
    with pytest.raises(R.RetentionError, match="archive check failed"):
        R.archive_old_events(db, tmp_path / "arch", 1, dry_run=False, now=NOW)
    assert remaining(db) == before and not list((tmp_path / "arch").glob("*.jsonl.gz"))


def test_refuses_replay_database_no_checkpoint_and_bad_age(db, tmp_path):
    with pytest.raises(R.RetentionError, match="no live patients"):
        R.archive_old_events(db, tmp_path, 1)
    with db.begin() as c:
        S.upsert(c, S.live_patient, ["patient_id"], patient_id="A", source="device", first_wall=1.0)
    with pytest.raises(R.RetentionError, match="checkpoint"):
        R.archive_old_events(db, tmp_path, 1)
    with pytest.raises(R.RetentionError, match="at least 1"):
        R.archive_old_events(db, tmp_path, 0)


def test_an_alert_that_appears_after_the_selection_still_protects_its_event(db, tmp_path, monkeypatch):
    ids = seed(db)
    orig_open, state = R.gzip.open, {"done": False}

    def opener(path, mode="rb", *a, **k):          # the read-back verification runs after the selection and before the delete
        if "r" in mode and not state["done"]:
            state["done"] = True
            with db.begin() as c:
                c.execute(S.alerts.insert().values(alert_id="A|late", patient_id="A", type="fall", time=1.0, payload="{}", status="SENT", event_id=ids["plain_accel"], event_time="x"))
        return orig_open(path, mode, *a, **k)
    monkeypatch.setattr(R.gzip, "open", opener)
    R.archive_old_events(db, tmp_path / "arch", 1, dry_run=False, now=NOW)
    assert ids["plain_accel"] in remaining(db)


def test_cli_dry_run_by_default_and_yes_to_apply(tmp_path, capsys):
    p = tmp_path / "c.db"
    ids = seed(S.get_engine(p))
    n = len(remaining(S.get_engine(p)))
    assert CLI.main(["--db", str(p), "--older-than-days", "1", "--out-dir", str(tmp_path / "o")]) == 0
    assert "nothing was changed" in capsys.readouterr().out and len(remaining(S.get_engine(p))) == n
    assert CLI.main(["--db", str(p), "--older-than-days", "1", "--out-dir", str(tmp_path / "o"), "--yes"]) == 0
    assert len(remaining(S.get_engine(p))) < n and list((tmp_path / "o").glob("*.jsonl.gz"))
    assert CLI.main(["--db", str(tmp_path / "empty.db"), "--older-than-days", "1"]) == 1


def test_restart_recovery_after_archiving_changes_nothing(live_eng):
    """Emulated days -> archive the closed day's raw rows -> a fresh processor recovers and the decisions and provisional flags are identical."""
    eng = live_eng
    proc, d = build(eng)
    emu = Emulator(eng, cfg={"speed": 6 * 3600})
    emu.load(start_day="2019-05-13", n_days=3, n_patients=1)
    emu.status = "playing"
    emu.tick(wall=0.0)
    run_emulator(eng, proc, emu, wall_steps=10)                                 # day 1 and 2 closed, day 3 open

    def snap():
        with eng.begin() as c:
            return sorted((r.patient_id, r.day, r.status, bool(r.provisional)) for r in c.execute(select(S.case_state)))
    before = snap()
    with eng.begin() as c:
        n0 = c.execute(select(func.count()).select_from(S.event_log)).scalar()
        open_days = {r.day for r in c.execute(select(S.live_day.c.day).where(S.live_day.c.closed == 0))}
    r = R.archive_old_events(eng, eng.url.database.rsplit("/", 1)[0] + "/arch", 1, dry_run=False, now=NOW)
    assert r["deleted"] > 0
    with eng.begin() as c:
        left = c.execute(select(func.count()).select_from(S.event_log)).scalar()
        assert left == n0 - r["deleted"]
        still_open = c.execute(select(func.count()).select_from(S.event_log).where(S.event_log.c.day.in_(open_days))).scalar()
    assert still_open > 0                                                       # the open day's raw events are all still there
    proc2, _ = build(eng)
    proc2.recover()
    assert snap() == before
    d.flush(5)
