"""SQLite state (SQLAlchemy Core). Safe for several processes: WAL + busy timeout; every write is idempotent."""
from __future__ import annotations

import json
import time
from pathlib import Path

from sqlalchemy import (Boolean, Column, Float, Integer, MetaData, String, Table, Text, UniqueConstraint,
                        create_engine, event, text)
from sqlalchemy.dialects.sqlite import insert

from echos.config import ROOT, load_config

md = MetaData()
agent_heartbeat = Table("agent_heartbeat", md, Column("agent_id", String, primary_key=True), Column("ts", Float),
                        Column("last_event_id", Integer), Column("status", String), Column("pid", Integer))
agent_checkpoint = Table("agent_checkpoint", md, Column("agent_id", String, primary_key=True),
                         Column("last_event_id", Integer), Column("ts", Float))
event_log = Table("event_log", md, Column("event_id", Integer, primary_key=True, autoincrement=True),
                  Column("patient_id", String, index=True), Column("day", String, index=True), Column("ts", String),
                  Column("kind", String, index=True), Column("payload", Text))
agent_output = Table("agent_output", md, Column("event_id", Integer, primary_key=True),
                     Column("agent_id", String, primary_key=True), Column("patient_id", String),
                     Column("day", String, index=True), Column("result", Text), Column("done_ts", Float))
case_state = Table("case_state", md, Column("patient_id", String, primary_key=True), Column("day", String, primary_key=True),
                   Column("status", String), Column("data_gap", Boolean), Column("days_since_vitals", Integer),
                   Column("reasons", Text), Column("report", String), Column("updated_ts", Float))
alerts = Table("alerts", md, Column("alert_id", String, primary_key=True),   # unique per event: patient|day|type|event_id
               Column("patient_id", String, index=True), Column("type", String), Column("time", Float),
               Column("payload", Text), Column("status", String, default="PENDING"),   # PENDING / SENT / FAILED: derived from the outbox
               Column("event_id", Integer), Column("event_time", String))
# one row per (alert, recipient role): PENDING -> SENT | FAILED, up to 3 attempts. A failure stays visible (never looks delivered).
outbox = Table("outbox", md, Column("outbox_id", Integer, primary_key=True, autoincrement=True),
               Column("alert_id", String, index=True), Column("recipient_role", String), Column("address", String),
               Column("channel", String), Column("subject", Text), Column("body", Text), Column("body_html", Text),
               Column("status", String, default="PENDING"), Column("attempts", Integer, default=0), Column("error", Text),
               Column("event_time", String), Column("queued_time", Float), Column("sent_time", Float),
               Column("attachment_name", String), Column("attachment_path", String),   # weekly report PDF
               Column("next_attempt_time", Float), Column("last_attempt_time", Float), Column("window_start", Float),   # retry state (alert emails): survives a restart
               UniqueConstraint("alert_id", "recipient_role", name="uq_outbox_alert_role"))
gt_fall = Table("gt_fall", md, Column("event_id", Integer, primary_key=True), Column("patient_id", String),
                Column("day", String), Column("ts", String), Column("subject", String), Column("file", String))
restart_log = Table("restart_log", md, Column("id", Integer, primary_key=True, autoincrement=True),
                    Column("agent_id", String), Column("detected_ts", Float), Column("reason", String),
                    Column("restarted_ts", Float))

# which event kinds each agent consumes
AGENT_KINDS = {"quality": ["day"], "health_monitoring": ["day"], "fall": ["sensor"]}
# Agent 0 runs first: health monitoring waits for its output of the same event (unless its heartbeat is stale; then pass-through).
# The fall agent is independent of both.
AFTER_QUALITY = ("health_monitoring",)


def db_url(path=None) -> str:
    p = Path(path) if path else ROOT / load_config()["db_path"]
    p.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{p}"


def get_engine(path=None):
    eng = create_engine(db_url(path), connect_args={"timeout": 30})

    @event.listens_for(eng, "connect")
    def _pragma(dbapi_conn, _):
        c = dbapi_conn.cursor()
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.close()

    md.create_all(eng)
    with eng.begin() as c:
        create_feed_triggers(c)
    with eng.begin() as c:   # databases created before these columns existed
        for table, col, typ in (("reports", "provenance", "TEXT"), ("outbox", "attachment_name", "TEXT"), ("outbox", "attachment_path", "TEXT"),
                                ("outbox", "next_attempt_time", "REAL"), ("outbox", "last_attempt_time", "REAL"), ("outbox", "window_start", "REAL")):
            if col not in {r[1] for r in c.execute(text(f"PRAGMA table_info({table})"))}:
                c.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {typ}"))
    return eng


def insert_ignore(conn, table, **vals) -> bool:
    """Idempotent insert; True if a new row was written."""
    return conn.execute(insert(table).values(**vals).prefix_with("OR IGNORE")).rowcount > 0


def upsert(conn, table, keys, **vals):
    stmt = insert(table).values(**vals)
    conn.execute(stmt.on_conflict_do_update(index_elements=keys, set_={k: v for k, v in vals.items() if k not in keys}))


def j(x) -> str:
    return json.dumps(x, default=str)


def now() -> float:
    return time.time()

# ---- Report agent tables (daily facts feed the reports and the dashboards; reports index points to files in reports/)
daily_facts = Table("daily_facts", md, Column("patient_id", String, primary_key=True), Column("day", String, primary_key=True),
                    Column("facts", Text))
reports = Table("reports", md, Column("patient_id", String, primary_key=True), Column("day", String, primary_key=True),
                Column("kind", String, primary_key=True), Column("mode", String), Column("path", String),
                Column("validation", Text), Column("created_ts", Float),
                Column("provenance", Text))   # JSON: model / cache hit / tools / sources / validator / fallback reason / request counts ("How this report was made")


# ---- live control + change feed (API <-> runtime). The API never loads models: it writes commands here and reads state/feed.
sim_state = Table("sim_state", md, Column("key", String, primary_key=True), Column("value", Text))          # status / speed / cursor / total / sim_time / message
sim_command = Table("sim_command", md, Column("id", Integer, primary_key=True, autoincrement=True), Column("ts", Float), Column("cmd", String),
                    Column("args", Text), Column("status", String, default="NEW"), Column("result", Text))  # NEW -> DONE | ERROR (consumed by echos.sim.controller)
event_feed = Table("event_feed", md, Column("id", Integer, primary_key=True, autoincrement=True), Column("ts", Float), Column("kind", String),
                   Column("data", Text))                                                                      # server-sent events: filled by triggers below

_TRIGGERS = [
    ("feed_alert", "AFTER INSERT ON alerts", "", "'alert'", "json_object('alert_id', NEW.alert_id, 'patient_id', NEW.patient_id, 'type', NEW.type, 'event_time', NEW.event_time)"),
    ("feed_day_ins", "AFTER INSERT ON case_state", "", "'day_status'", "json_object('patient_id', NEW.patient_id, 'day', NEW.day, 'status', NEW.status)"),
    ("feed_day_upd", "AFTER UPDATE OF status ON case_state", "WHEN OLD.status IS NOT NEW.status", "'day_status'",
     "json_object('patient_id', NEW.patient_id, 'day', NEW.day, 'status', NEW.status, 'previous', OLD.status)"),
    ("feed_report_ins", "AFTER INSERT ON reports", "", "'report_ready'", "json_object('patient_id', NEW.patient_id, 'day', NEW.day, 'kind', NEW.kind, 'mode', NEW.mode)"),
    ("feed_report_upd", "AFTER UPDATE ON reports", "", "'report_ready'", "json_object('patient_id', NEW.patient_id, 'day', NEW.day, 'kind', NEW.kind, 'mode', NEW.mode)"),
    ("feed_outbox_ins", "AFTER INSERT ON outbox", "", "'outbox_status'",
     "json_object('outbox_id', NEW.outbox_id, 'alert_id', NEW.alert_id, 'role', NEW.recipient_role, 'status', NEW.status)"),
    ("feed_outbox_upd", "AFTER UPDATE OF status ON outbox", "WHEN OLD.status IS NOT NEW.status", "'outbox_status'",
     "json_object('outbox_id', NEW.outbox_id, 'alert_id', NEW.alert_id, 'role', NEW.recipient_role, 'status', NEW.status)"),
    ("feed_agent_status", "AFTER UPDATE OF status ON agent_heartbeat", "WHEN OLD.status IS NOT NEW.status", "'agent_health'",
     "json_object('agent', NEW.agent_id, 'status', NEW.status, 'alive', 1)"),
]


def create_feed_triggers(conn) -> None:
    """Every write by ANY process to alerts / case_state / reports / outbox / agent_heartbeat adds a row to event_feed."""
    for name, when, cond, kind, data in _TRIGGERS:
        conn.execute(text(f"CREATE TRIGGER IF NOT EXISTS {name} {when} {cond} BEGIN INSERT INTO event_feed (ts, kind, data) "
                          f"VALUES (strftime('%s','now') + 0.0, {kind}, {data}); END"))
