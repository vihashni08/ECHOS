"""Read-side data access for the API: SQLite store + metrics JSON. No model is imported or loaded here."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from sqlalchemy import func, select

from echos.config import ROOT, load_config
from echos.data.age import band_label
from echos.orchestrator import state as S

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
STATUS_TEXT = {"NORMAL": "Nothing unusual", "WATCH": "Above their usual, not reviewed today", "REVIEW": "On today's review list",
               "URGENT": "Red-flag reading: urgent alert sent", "PENDING": "Scored, waiting for the day's review decision"}


class NotFound(Exception):
    pass


def mask_email(a: Optional[str]) -> str:
    if not a:
        return ""
    if "@" not in a:
        return "***"
    u, d = a.split("@", 1)
    return f"{u[:2]}***@{d}"


def scrub(text: Optional[str]) -> Optional[str]:
    """Any address inside free text (e.g. an error message) is masked."""
    return None if text is None else _EMAIL.sub(lambda m: mask_email(m.group(0)), text)


@dataclass
class Ctx:
    eng: Any
    root: Path = ROOT
    cfg: dict = field(default_factory=load_config)
    metrics_dir: Optional[Path] = None
    demo: Optional[pd.DataFrame] = None
    allow_kill: bool = False
    dispatcher: Any = None                       # AlertDispatcher (created lazily; manual outbox retries)
    health_state: dict = field(default_factory=dict)   # last alive/dead state per agent seen by the event stream
    lock: Any = None

    def __post_init__(self):
        import threading
        self.lock = threading.Lock()
        self.metrics_dir = Path(self.metrics_dir) if self.metrics_dir else self.root / self.cfg["paths"]["metrics"]

    def demo_df(self) -> pd.DataFrame:
        if self.demo is None:
            from echos.data.loaders import load_demographics
            self.demo = load_demographics()
        return self.demo


# ------------------------------------------------------------------ helpers
def page(rows: list, limit: int, offset: int) -> tuple[list, int]:
    return rows[offset:offset + limit], len(rows)


def patient_ids(ctx: Ctx) -> list[str]:
    with ctx.eng.begin() as c:
        a = {r[0] for r in c.execute(select(S.case_state.c.patient_id).distinct())}
        b = {r[0] for r in c.execute(select(S.event_log.c.patient_id).distinct())}
        sp = c.execute(select(S.sim_state.c.value).where(S.sim_state.c.key == "patients")).scalar()    # patients of the loaded simulation, before any event
    return sorted(x for x in a | b | set(json.loads(sp) if sp else []) if x)      # day_close events carry no patient


def require_patient(ctx: Ctx, pid: str) -> None:
    if pid not in patient_ids(ctx):
        raise NotFound(f"unknown patient {pid!r}")


def demo_of(ctx: Ctx, pid: str) -> tuple[str, str]:
    try:
        d = ctx.demo_df()
        r = d[d.patient_id == pid]
        return (band_label(r.age_band.iloc[0]) if len(r) else "unknown"), (str(r.sex.iloc[0]) if len(r) else "unknown")
    except Exception:  # noqa: BLE001 - demographics are optional
        return "unknown", "unknown"


def list_patients(ctx: Ctx) -> list[dict]:
    out = []
    with ctx.eng.begin() as c:
        last = {r.patient_id: r for r in c.execute(select(S.case_state.c.patient_id, S.case_state.c.day, S.case_state.c.status).order_by(S.case_state.c.day))}
        al = {r.patient_id: r for r in c.execute(select(S.alerts.c.patient_id, S.alerts.c.type, S.alerts.c.event_time, S.alerts.c.time).order_by(S.alerts.c.time))}
        days = dict(c.execute(select(S.case_state.c.patient_id, func.count()).group_by(S.case_state.c.patient_id)).fetchall())
        reviews = dict(c.execute(select(S.review_list.c.patient_id, func.count()).group_by(S.review_list.c.patient_id)).fetchall())
    for pid in patient_ids(ctx):
        age, sex = demo_of(ctx, pid)
        la, a = last.get(pid), al.get(pid)
        out.append({"patient_id": pid, "age_band": age, "sex": sex, "latest_status": la.status if la else None, "latest_day": la.day if la else None,
                    "last_alert_time": a.event_time if a else None, "last_alert_type": a.type if a else None, "days": int(days.get(pid, 0)),
                    "reviews": int(reviews.get(pid, 0))})
    return out


def latest_day(ctx: Ctx, pid: str) -> Optional[str]:
    with ctx.eng.begin() as c:
        return c.execute(select(func.max(S.case_state.c.day)).where(S.case_state.c.patient_id == pid)).scalar()


def _reasons(raw: Optional[str]) -> list[str]:
    try:
        return [str(x) for x in json.loads(raw or "[]")]
    except (TypeError, ValueError):
        return []


def statuses(ctx: Ctx, pid: str, days: int) -> list[dict]:
    require_patient(ctx, pid)
    with ctx.eng.begin() as c:
        rows = c.execute(select(S.case_state.c.day, S.case_state.c.status, S.case_state.c.reasons).where(S.case_state.c.patient_id == pid)
                         .order_by(S.case_state.c.day.desc()).limit(days)).fetchall()
    return [{"day": d, "status": st, "reasons": _reasons(r)} for d, st, r in reversed(rows)]


def alert_out(r) -> dict:
    return {"alert_id": r.alert_id, "patient_id": r.patient_id, "type": r.type, "status": r.status, "event_id": r.event_id, "event_time": r.event_time, "time": r.time}


def outbox_out(r) -> dict:
    return {"kind": "weekly" if str(r.alert_id).startswith("weekly|") else "alert", "delivery": "email" if r.channel == "smtp" else "dry_run", "outbox_id": r.outbox_id, "alert_id": r.alert_id, "recipient_role": r.recipient_role, "address": mask_email(r.address), "channel": r.channel,
            "subject": r.subject, "status": r.status, "attempts": r.attempts or 0, "error": scrub(r.error), "event_time": r.event_time, "queued_time": r.queued_time,
            "sent_time": r.sent_time, "attachment_name": r.attachment_name, **retry_state(r)}


def retry_state(r) -> dict:
    """Retry state of one outbox row, from the table only: next attempt, last attempt, end of the automatic retry window (alert mails), whether a
    manual retry is possible."""
    a = load_config().get("alerts", {})
    weekly = str(r.alert_id).startswith("weekly|")
    ws = getattr(r, "window_start", None) or r.queued_time
    return {"next_attempt_time": r.next_attempt_time if r.status in ("RETRYING", "PENDING") else None, "last_attempt_time": r.last_attempt_time,
            "retry_window_end": None if weekly or ws is None else ws + float(a.get("retry_window_s", 900)),
            "sweep_until": None if weekly or r.queued_time is None else r.queued_time + float(a.get("sweep_window_s", 7200)),
            "can_retry": bool(r.status in ("FAILED", "RETRYING", "PENDING") and r.address)}


def list_alerts(ctx: Ctx, pid: Optional[str], limit: int, offset: int) -> tuple[list, int]:
    q = select(S.alerts).order_by(S.alerts.c.time.desc())
    cnt = select(func.count()).select_from(S.alerts)
    if pid:
        q, cnt = q.where(S.alerts.c.patient_id == pid), cnt.where(S.alerts.c.patient_id == pid)
    with ctx.eng.begin() as c:
        return [alert_out(r) for r in c.execute(q.limit(limit).offset(offset))], c.execute(cnt).scalar()


def alert_by_id(ctx: Ctx, alert_id: str) -> dict:
    with ctx.eng.begin() as c:
        r = c.execute(select(S.alerts).where(S.alerts.c.alert_id == alert_id)).first()
    if r is None:
        raise NotFound(f"unknown alert {alert_id!r}")
    return alert_out(r)


def list_outbox(ctx: Ctx, status: Optional[str], limit: int, offset: int) -> tuple[list, int]:
    q = select(S.outbox).order_by(S.outbox.c.queued_time.desc())
    cnt = select(func.count()).select_from(S.outbox)
    if status:
        q, cnt = q.where(S.outbox.c.status == status), cnt.where(S.outbox.c.status == status)
    with ctx.eng.begin() as c:
        return [outbox_out(r) for r in c.execute(q.limit(limit).offset(offset))], c.execute(cnt).scalar()


def sim_state(ctx: Ctx) -> dict:
    with ctx.eng.begin() as c:
        kv = {r.key: json.loads(r.value) for r in c.execute(select(S.sim_state))}
    alive = bool(kv.get("updated")) and time.time() - kv["updated"] < 15
    return {"status": kv.get("status", "idle") if kv else "idle", "speed": kv.get("speed"), "cursor": kv.get("cursor"), "total": kv.get("total"), "sim_time": kv.get("sim_time"),
            "message": kv.get("message") or "", "runtime_alive": alive}


def send_command(ctx: Ctx, cmd: str, args: dict, wait_s: float) -> tuple[int, Optional[str], Optional[str]]:
    with ctx.eng.begin() as c:
        cid = c.execute(S.sim_command.insert().values(ts=time.time(), cmd=cmd, args=json.dumps(args), status="NEW")).inserted_primary_key[0]
    end = time.time() + wait_s
    while time.time() < end:
        with ctx.eng.begin() as c:
            r = c.execute(select(S.sim_command.c.status, S.sim_command.c.result).where(S.sim_command.c.id == cid)).first()
        if r.status != "NEW":
            return cid, r.status, r.result
        time.sleep(0.05)
    return cid, "NEW", None


def agent_health(ctx: Ctx) -> list[dict]:
    hb_s = ctx.cfg["runtime"]["heartbeat_s"]
    with ctx.eng.begin() as c:
        hbs = {r.agent_id: r for r in c.execute(select(S.agent_heartbeat))}
        rs = {r[0]: r[1] for r in c.execute(select(S.restart_log.c.agent_id, func.count()).group_by(S.restart_log.c.agent_id))}
    out = []
    from echos.team.run_agent import AGENTS
    for a in AGENTS:
        h = hbs.get(a)
        age = None if h is None else max(0.0, time.time() - h.ts)
        out.append({"agent": a, "status": h.status if h else "missing", "alive": bool(h and h.status != "stopped" and age < 3 * hb_s + 10), "heartbeat_age_s": None if age is None else round(age, 1),
                    "last_event_id": h.last_event_id if h else None, "pid": h.pid if h else None, "restarts": int(rs.get(a, 0))})
    return out


# ------------------------------------------------------------------ system
def read_metrics(ctx: Ctx, names: Optional[list[str]]) -> dict:
    avail = sorted(p.stem for p in ctx.metrics_dir.glob("*.json"))
    want = names or [n for n in ("v2_label_audit", "v2_vitals", "v2_agitation", "v2_allocation", "v2_runtime", "v2_evidence") if n in avail]
    return {"available": avail, "files": {n: json.loads((ctx.metrics_dir / f"{n}.json").read_text()) for n in want if n in avail}}


def limitations(ctx: Ctx) -> dict:
    p = ctx.root / "docs" / "METHOD.md"
    if not p.exists():
        raise NotFound("docs/METHOD.md not found")
    m = re.search(r"^## \d+\. Limitations\s*\n(.*?)(?=^## |\Z)", p.read_text(), re.S | re.M)
    if not m:
        raise NotFound("no Limitations section in docs/METHOD.md")
    return {"source": "docs/METHOD.md (Limitations)", "text": m.group(1).strip()}
