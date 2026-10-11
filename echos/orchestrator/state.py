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
alerts = Table("alerts", md, Column("alert_id", String, primary_key=True),   # unique per event: patient|day|type
               Column("patient_id", String, index=True), Column("type", String), Column("time", Float),
               Column("payload", Text), Column("status", String, default="SENT"))
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
                Column("validation", Text), Column("created_ts", Float))
