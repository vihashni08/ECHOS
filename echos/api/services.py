"""Read-side data access for the API: SQLite store + saved reports + metrics JSON. No model is imported or loaded here."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import pandas as pd
from sqlalchemy import func, select

from echos.config import ROOT, load_config
from echos.orchestrator import state as S
from echos.reports import facts as F
from echos.reports import weekly_data as WD

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
STATUS_TEXT = {"NORMAL": "Normal", "MONITOR": "Changes noticed", "URGENT": "Needs attention"}


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
    pdy: Optional[pd.DataFrame] = None
    phys: Optional[pd.DataFrame] = None
    demo: Optional[pd.DataFrame] = None
    activity: Optional[pd.DataFrame] = None
    allow_kill: bool = False
    report_root: Optional[Path] = None           # where report files live (default: root); a dev copy of the database has its own
    dispatcher: Any = None                       # AlertDispatcher (created lazily; shared worker pool)
    weekly_kwargs: dict = field(default_factory=dict)   # tests: budget=..., retriever=..., frames
    health_state: dict = field(default_factory=dict)
    sisfall_files: Optional[pd.DataFrame] = None
    lock: Any = None

    def __post_init__(self):
        import threading
        self.lock = threading.Lock()
        self.report_root = Path(self.report_root).resolve() if self.report_root else self.root
        self.metrics_dir = Path(self.metrics_dir) if self.metrics_dir else self.root / self.cfg["paths"]["metrics"]

    # lazily loaded frames (cached for the process)
    def pdy_df(self) -> pd.DataFrame:
        if self.pdy is None:
            self.pdy = F.patient_days()
        from echos.live import store as LS
        return LS.live_pdy(self.eng, self.pdy)          # training table + live day rows (live mode); unchanged when there are none

    def phys_df(self) -> pd.DataFrame:
        if self.phys is None:
            from echos.data.loaders import load_physiology
            self.phys = load_physiology()
        from echos.live import store as LS
        return LS.merge_live(self.eng, self.phys, "vitals")

    def demo_df(self) -> pd.DataFrame:
        if self.demo is None:
            from echos.data.loaders import load_demographics
            self.demo = load_demographics()
        return self.demo

    def act_df(self) -> pd.DataFrame:
        if self.activity is None:
            from echos.data.loaders import load_activity
            self.activity = load_activity()
        from echos.live import store as LS
        return LS.merge_live(self.eng, self.activity, "activity")


# ------------------------------------------------------------------ helpers
def page(rows: list, limit: int, offset: int) -> tuple[list, int]:
    return rows[offset:offset + limit], len(rows)


def patient_ids(ctx: Ctx) -> list[str]:
    with ctx.eng.begin() as c:
        a = {r[0] for r in c.execute(select(S.case_state.c.patient_id).distinct())}
        b = {r[0] for r in c.execute(select(S.event_log.c.patient_id).distinct())}
        sp = c.execute(select(S.sim_state.c.value).where(S.sim_state.c.key == "patients")).scalar()    # patients of the loaded simulation, before any event
    return sorted(a | b | set(json.loads(sp) if sp else []))


def require_patient(ctx: Ctx, pid: str) -> None:
    if pid not in patient_ids(ctx):
        raise NotFound(f"unknown patient {pid!r}")


def demo_of(ctx: Ctx, pid: str) -> tuple[str, str]:
    try:
        d = ctx.demo_df()
        r = d[d.patient_id == pid]
        nums = re.findall(r"\d+", str(r.age_band.iloc[0])) if len(r) else []
        return (f"{nums[0]}-{nums[1]}" if len(nums) >= 2 else "unknown"), (str(r.sex.iloc[0]) if len(r) else "unknown")
    except Exception:  # noqa: BLE001 - demographics are optional
        return "unknown", "unknown"


def facts_of(ctx: Ctx, pid: str, day: str) -> Optional[dict]:
    with ctx.eng.begin() as c:
        r = c.execute(select(S.daily_facts.c.facts).where((S.daily_facts.c.patient_id == pid) & (S.daily_facts.c.day == day))).first()
    return json.loads(r[0]) if r else None


def plain_reasons(facts: Optional[dict]) -> list[str]:
    """Plain-language 'why flagged' from the stored facts. Never a model probability or score."""
    out = []
    for f in (facts or {}).get("flags", []):
        t, e = f["type"], f["evidence"]
        if t in ("fall", "fall_inactivity"):
            out.append("A possible fall was detected" + (f" at {e['time']}" if e.get("time") else "") + (", followed by little movement." if e.get("post_fall_inactive") else "."))
        elif e.get("kind") == "critical":
            out.append("A reading in the critical range was recorded: " + ", ".join(f"{k} {e[k]:g}" for k in ("sys", "dia", "hr") if e.get(k) is not None) + ".")
        elif e.get("kind") == "vitals_alert":
            parts = []
            for k, n in (("sys", "Systolic blood pressure"), ("dia", "Diastolic blood pressure"), ("hr", "Heart rate")):
                now, base = e.get(f"{k}_now"), e.get(f"{k}_baseline")
                if now is not None and base is not None and now != base:
                    parts.append(f"{n} {now:g} was {'above' if now > base else 'below'} this person's usual range (usual {base:g})")
            out.append(("; ".join(parts) or "Blood pressure or heart rate differed from this person's usual") + ".")
        elif t in WD.UNUSUAL:
            out.append("Activity pattern differs from usual.")
        elif t == "data_gap":
            out.append("Data gap: " + str(e.get("note", "some data was missing")) + ".")
    return out


# ------------------------------------------------------------------ patients
def attention_kind(la) -> Optional[str]:
    """'change' = something differs from the person's usual (or a fall / critical reading); 'data' = the ONLY reasons are data problems (a silent sensor,
    no vitals for days, unchecked data): a device needs looking at, not the person. None = nothing to show."""
    if la is None or la.status not in ("MONITOR", "URGENT"):
        return None
    try:
        reasons = json.loads(la.reasons or "[]")
    except ValueError:
        reasons = []
    return "data" if la.status == "MONITOR" and reasons and all(str(r).startswith("DATA_GAP") for r in reasons) else "change"


def list_patients(ctx: Ctx) -> list[dict]:
    out = []
    with ctx.eng.begin() as c:
        last = {r.patient_id: r for r in c.execute(select(S.case_state.c.patient_id, S.case_state.c.day, S.case_state.c.status, S.case_state.c.reasons).order_by(S.case_state.c.day))}
        al = {r.patient_id: r for r in c.execute(select(S.alerts.c.patient_id, S.alerts.c.type, S.alerts.c.event_time, S.alerts.c.time).order_by(S.alerts.c.time))}
        fx = c.execute(select(S.daily_facts.c.patient_id, S.daily_facts.c.facts)).fetchall()
    comp: dict = {}
    for pid, f in fx:
        d = json.loads(f)
        x = comp.setdefault(pid, dict(days=0, days_with_activity=0, days_with_vitals=0, sensor_gap_days=0))
        x["days"] += 1
        x["days_with_activity"] += bool(d.get("has_activity"))
        x["days_with_vitals"] += bool(d.get("has_reading"))
        x["sensor_gap_days"] += bool((d.get("quality") or {}).get("sensor_gap"))
    for pid in patient_ids(ctx):
        age, sex = demo_of(ctx, pid)
        la, a = last.get(pid), al.get(pid)
        out.append({"attention_kind": attention_kind(la), "patient_id": pid, "age_band": age, "sex": sex, "latest_status": la.status if la else None, "latest_day": la.day if la else None,
                    "last_alert_time": a.event_time if a else None, "last_alert_type": a.type if a else None, "completeness": comp.get(pid, {})})
    return out


def latest_day(ctx: Ctx, pid: str) -> Optional[str]:
    with ctx.eng.begin() as c:
        return c.execute(select(func.max(S.case_state.c.day)).where(S.case_state.c.patient_id == pid)).scalar()


def summary(ctx: Ctx, pid: str, day: Optional[str]) -> dict:
    require_patient(ctx, pid)
    day = day or latest_day(ctx, pid)
    if day is None:
        raise NotFound(f"no processed day for patient {pid}")
    with ctx.eng.begin() as c:
        cs = c.execute(select(S.case_state).where((S.case_state.c.patient_id == pid) & (S.case_state.c.day == day))).first()
    if cs is None:
        raise NotFound(f"no status for patient {pid} on {day}")
    facts = facts_of(ctx, pid, day)
    note, alerts = None, []
    if facts:
        from echos.reports import agent as A
        note, alerts = A.daily_data_note(facts), A.daily_alerts(facts)
    return {"patient_id": pid, "day": day, "status": cs.status, "status_text": STATUS_TEXT.get(cs.status, cs.status), "reasons": plain_reasons(facts),
            "alerts": alerts, "data_note": note}


def statuses(ctx: Ctx, pid: str, days: int) -> list[dict]:
    require_patient(ctx, pid)
    with ctx.eng.begin() as c:
        rows = c.execute(select(S.case_state.c.day, S.case_state.c.status).where(S.case_state.c.patient_id == pid).order_by(S.case_state.c.day.desc()).limit(days)).fetchall()
    return [{"day": d, "status": st, "reasons": plain_reasons(facts_of(ctx, pid, d))} for d, st in reversed(rows)]


def _range(ctx: Ctx, pid: str, a: Optional[str], b: Optional[str], default_days: int = 14) -> tuple[pd.Timestamp, pd.Timestamp]:
    end = pd.Timestamp(b) if b else pd.Timestamp(latest_day(ctx, pid) or pd.Timestamp.today().date())
    start = pd.Timestamp(a) if a else end - pd.Timedelta(days=default_days - 1)
    if start > end:
        raise ValueError("'from' is after 'to'")
    return start.normalize(), end.normalize()


# ------------------------------------------------------------------ vitals / routine series (past-only personal bands)
def vitals(ctx: Ctx, pid: str, a: Optional[str], b: Optional[str]) -> dict:
    require_patient(ctx, pid)
    start, end = _range(ctx, pid, a, b)
    cfg, pdy = ctx.cfg, ctx.pdy_df()
    pdf = pdy[pdy.patient_id == pid].sort_values("day")
    ph = ctx.phys_df()
    ph = ph[(ph.patient_id == pid) & ph.device_type.isin(WD.DEVICE)].copy()
    ph["date"] = pd.to_datetime(ph["date"])
    ph = ph[(ph.date >= start) & (ph.date < end + pd.Timedelta(days=1))].sort_values("date")
    ranges = cfg["data_quality"]["ranges"]
    ok = [ranges.get(d, (-1e9, 1e9))[0] <= v <= ranges.get(d, (-1e9, 1e9))[1] for d, v in zip(ph.device_type, ph.value)]
    ph = ph[pd.Series(ok, index=ph.index, dtype=bool)]
    days = pd.date_range(start, end)
    series = {}
    for var, (name, unit) in WD.VARS.items():
        bands = {d: WD._band(pdf, var, d, cfg) for d in days}
        pts = []
        for r in ph[ph.device_type.map(WD.DEVICE) == var].itertuples():
            bd = bands.get(r.date.normalize())
            pts.append({"t": r.date.strftime("%Y-%m-%d %H:%M"), "value": round(float(r.value), 1), "base": WD._r(bd[0]) if bd else None,
                        "lo": WD._r(bd[2]) if bd else None, "hi": WD._r(bd[3]) if bd else None, "flagged": bool(bd and not (bd[2] <= r.value <= bd[3]))})
        est = any(v is not None for v in bands.values())
        series[var] = {"name": name, "unit": unit, "baseline_established": est, "baseline_note": None if est else WD.NOT_ESTABLISHED, "points": pts,
                       "bands": [{"day": d.strftime("%Y-%m-%d"), "lo": WD._r(v[2]), "hi": WD._r(v[3]), "base": WD._r(v[0])} for d, v in bands.items() if v]}
    return {"patient_id": pid, "start": start.strftime("%Y-%m-%d"), "end": end.strftime("%Y-%m-%d"), "min_history_readings": WD.MIN_VITAL_HISTORY, "series": series}


ROOMS = ["Kitchen", "Hallway", "Lounge", "Bedroom", "Fridge Door", "Bathroom", "Front Door", "Back Door"]


def routine(ctx: Ctx, pid: str, a: Optional[str], b: Optional[str]) -> dict:
    require_patient(ctx, pid)
    start, end = _range(ctx, pid, a, b)
    cfg, pdy = ctx.cfg, ctx.pdy_df()
    pdf = pdy[pdy.patient_id == pid].sort_values("day")
    act = ctx.act_df()
    act = act[(act.patient_id == pid) & (act.date >= start) & (act.date < end + pd.Timedelta(days=1))]
    counts = [[0] * 24 for _ in ROOMS]
    for (room, hour), n in act.groupby(["location_name", act.date.dt.hour]).size().items():
        if room in ROOMS:
            counts[ROOMS.index(room)][int(hour)] = int(n)
    days = []
    for d in pd.date_range(start, end):
        row = pdf[pdf.day == d]
        has = bool(len(row) and row.iloc[0].has_activity)
        m = {}
        for k in WD.ROUTINE:
            val = WD._r(row.iloc[0][k], 0) if has else None
            bs, sd = F._past_baseline(pdf, k, d, cfg["agent2"]["baseline_window_days"], cfg["agent2"]["min_baseline_days"], True)
            band = bs is not None and sd is not None
            m[k] = {"value": val, "base": WD._r(bs) if band else None, "lo": WD._r(bs - 2 * sd) if band else None, "hi": WD._r(bs + 2 * sd) if band else None,
                    "z": WD._r((val - bs) / sd, 1) if band and val is not None and sd > 0 else None}
        days.append({"day": d.strftime("%Y-%m-%d"), "has_activity": has, "metrics": m})
    return {"patient_id": pid, "start": start.strftime("%Y-%m-%d"), "end": end.strftime("%Y-%m-%d"), "rooms": ROOMS, "hours": list(range(24)), "counts": counts, "days": days,
            "min_history_days": cfg["agent2"]["min_baseline_days"], "baseline_note": WD.ROUTINE_NOT_ESTABLISHED}


# ------------------------------------------------------------------ reports
def _inside(ctx: Ctx, rel: str) -> Path:
    p = (ctx.report_root / rel).resolve()
    if ctx.report_root.resolve() not in p.parents:
        raise NotFound("report file outside the project")
    return p


_CITE = re.compile(r"\s*\[(?:F\d+|E\d+)(?:, (?:F\d+|E\d+))*\]")


def parse_daily(text: str) -> dict:
    """Structured view of the stored markdown daily report: status, alerts, changes, monitor items, data note (citation tags removed)."""
    secs: dict = {}
    cur = None
    for line in text.splitlines():
        if line.startswith("## "):
            cur = line[3:].strip().lower()
            secs[cur] = []
        elif line.startswith("---"):
            cur = None
        elif cur is not None and line.strip():
            secs[cur].append(_CITE.sub("", line.strip().removeprefix("- ")).strip())
    pick = lambda prefix: next((v for k, v in secs.items() if k.startswith(prefix)), [])  # noqa: E731
    m = re.search(r"\*\*Status: (\w+)\*\*", text)
    note = " ".join(pick("data note"))
    return {"status": m.group(1) if m else None, "alerts": pick("alerts"), "changes": pick("what changed"), "monitor": pick("what to monitor"), "data_note": note or None}


def daily_report(ctx: Ctx, pid: str, day: Optional[str]) -> dict:
    require_patient(ctx, pid)
    day = day or latest_day(ctx, pid)
    with ctx.eng.begin() as c:
        r = c.execute(select(S.reports).where((S.reports.c.patient_id == pid) & (S.reports.c.day == day) & (S.reports.c.kind == "daily"))).first()
    if r is None:
        raise NotFound(f"no daily report for {pid} on {day}")
    p = _inside(ctx, r.path)
    text = p.read_text() if p.exists() else ""
    return {"patient_id": pid, "day": day, "mode": r.mode, "text": text, **parse_daily(text), "created_ts": r.created_ts,
            "validation": json.loads(r.validation) if r.validation else None, "provenance": json.loads(r.provenance) if r.provenance else None}


def week_end_of(week: Optional[str], ctx: Ctx, pid: str) -> tuple[str, str, str]:
    if week is None:       # demo default: the latest stored weekly report whose vitals baselines are established, else the latest week with data
        with ctx.eng.begin() as c:
            rr = c.execute(select(S.reports.c.day, S.reports.c.provenance).where((S.reports.c.patient_id == pid) & (S.reports.c.kind == "weekly_pdf"))
                           .order_by(S.reports.c.day.desc())).fetchall()
        for day_, pv in rr:
            if pv and json.loads(pv).get("vitals_baseline_established"):
                week = WD.iso_week(day_)
                break
    if week is None:
        d = latest_day(ctx, pid)
        if d is None:
            raise NotFound(f"no data for patient {pid}")
        week = WD.iso_week(d)
    try:
        mon, sun = WD.week_bounds(week)
    except Exception as ex:  # noqa: BLE001
        raise ValueError(f"week must look like 2019-W19: {ex}")
    return WD.iso_week(mon), mon.strftime("%Y-%m-%d"), sun.strftime("%Y-%m-%d")


def weekly_row(ctx: Ctx, pid: str, week: Optional[str]):
    require_patient(ctx, pid)
    wk, mon, sun = week_end_of(week, ctx, pid)
    with ctx.eng.begin() as c:
        r = c.execute(select(S.reports).where((S.reports.c.patient_id == pid) & (S.reports.c.day == sun) & (S.reports.c.kind == "weekly_pdf"))).first()
    return wk, mon, sun, r


def weekly_weeks(ctx: Ctx, pid: str) -> list[str]:
    require_patient(ctx, pid)
    with ctx.eng.begin() as c:
        days = [r[0] for r in c.execute(select(S.reports.c.day).where((S.reports.c.patient_id == pid) & (S.reports.c.kind == "weekly_pdf")).order_by(S.reports.c.day))]
    return [WD.iso_week(d) for d in days]


def _pdf_exists(ctx: Ctx, path: Optional[str]) -> bool:
    try:
        return bool(path) and _inside(ctx, path).exists()
    except NotFound:       # a report row copied from another database may point outside this one's files
        return False


def _findings_with_sources(prov: dict):
    """Finding -> sources mapping with the stored source details attached (never abstract text)."""
    if prov.get("findings") is None:
        return None
    by_id = {s["id"]: s for s in prov.get("sources_retrieved") or []}
    return [{**f, "source_objs": [by_id[i] for i in f.get("sources", []) if i in by_id]} for f in prov["findings"]]


def weekly_report(ctx: Ctx, pid: str, week: Optional[str]) -> dict:
    wk, mon, sun, r = weekly_row(ctx, pid, week)
    if r is None:
        raise NotFound(f"no weekly report for {pid} in {wk} (POST .../weekly/send builds one)")
    prov = json.loads(r.provenance) if r.provenance else {}
    data = None
    try:
        kw = {k: v for k, v in {"pdy": ctx.pdy, "phys": ctx.phys, "demo": ctx.demo}.items() if v is not None}
        data = WD.narrative_payload(WD.build_weekly_data(ctx.eng, pid, wk, **kw))
    except Exception:  # noqa: BLE001 - data is a convenience; the stored report stands without it
        pass
    return {"patient_id": pid, "week": wk, "week_start": mon, "week_end": sun, "mode": r.mode, "narrative": prov.get("narrative"), "provenance": prov,
            "validation": json.loads(r.validation) if r.validation else None, "pdf_available": _pdf_exists(ctx, r.path),
            "pdf_pages": (prov.get("pdf") or {}).get("pages"), "data": data,
            "findings": _findings_with_sources(prov), "considerations": prov.get("considerations")}


# ------------------------------------------------------------------ alerts, outbox, falls
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


def _fall_rows(ctx: Ctx) -> list:
    thr = ctx.cfg["triage"]["fall_prob"]
    with ctx.eng.begin() as c:
        rows = c.execute(select(S.agent_output.c.event_id, S.agent_output.c.patient_id, S.agent_output.c.day, S.agent_output.c.result, S.event_log.c.ts, S.event_log.c.payload)
                         .select_from(S.agent_output.join(S.event_log, S.event_log.c.event_id == S.agent_output.c.event_id))
                         .where(S.agent_output.c.agent_id == "fall").order_by(S.event_log.c.ts.desc())).fetchall()
    return [r for r in rows if json.loads(r.result)["score"] >= thr]


def _fall_item(ctx: Ctx, r) -> dict:
    res, pl = json.loads(r.result), json.loads(r.payload or "{}")
    ev = res.get("evidence", {})
    with ctx.eng.begin() as c:
        st = c.execute(select(S.case_state.c.status).where((S.case_state.c.patient_id == r.patient_id) & (S.case_state.c.day == r.day))).scalar()
        from echos.orchestrator.alerts import alert_id
        al = c.execute(select(S.alerts.c.status).where(S.alerts.c.alert_id == alert_id(r.patient_id, r.day, "fall", r.event_id))).scalar()
    return {"id": r.event_id, "patient_id": r.patient_id, "day": r.day, "ts": r.ts, "sisfall_subject": pl.get("subject"), "probability": round(res["score"], 3),
            "probability_text": WD.fmt_prob(res["score"]), "peak_g": ev.get("peak_svm_g"), "inactivity": bool(ev.get("post_fall_inactive")), "day_status": st, "alert_status": al}


def list_falls(ctx: Ctx, limit: int, offset: int) -> tuple[list, int]:
    rows = _fall_rows(ctx)
    sel, n = page(rows, limit, offset)
    return [_fall_item(ctx, r) for r in sel], n


def downsample(a, fs: int, target: int = 200) -> dict:
    import numpy as np
    n = len(a)
    stride = max(1, -(-n // target))
    mag = np.sqrt((a ** 2).sum(axis=1))
    pk = int(np.argmax(mag))
    idx = sorted(set(range(0, n, stride)) | {pk})
    return {"fs_hz": fs, "downsample_stride": stride, "t_s": [round(i / fs, 3) for i in idx], "x_g": [round(float(a[i, 0]), 3) for i in idx],
            "y_g": [round(float(a[i, 1]), 3) for i in idx], "z_g": [round(float(a[i, 2]), 3) for i in idx], "magnitude_g": [round(float(mag[i]), 3) for i in idx],
            "impact_index": idx.index(pk), "impact_time_s": round(pk / fs, 3)}


def fall_detail(ctx: Ctx, fid: int) -> dict:
    row = next((r for r in _fall_rows(ctx) if r.event_id == fid), None)
    if row is None:
        raise NotFound(f"unknown fall {fid}")
    item = _fall_item(ctx, row)
    res, pl = json.loads(row.result), json.loads(row.payload or "{}")
    ev, a3 = res.get("evidence", {}), ctx.cfg["agent3"]
    from echos.orchestrator.alerts import alert_id
    aid = alert_id(row.patient_id, row.day, "fall", row.event_id)
    with ctx.eng.begin() as c:
        al = c.execute(select(S.alerts).where(S.alerts.c.alert_id == aid)).first()
        ob = c.execute(select(S.outbox).where(S.outbox.c.alert_id == aid).order_by(S.outbox.c.outbox_id)).fetchall()
    wave = None
    try:
        from echos.data import sisfall as sf
        n = int(a3["window_s"] * a3["fs"])
        arr = sf.load_file(pl["file"])[pl["win_start"]:pl["win_start"] + n]
        wave = downsample(arr, a3["fs"])
    except Exception:  # noqa: BLE001 - the recording may not be on this machine
        pass
    hold = set(ctx.cfg["replay"]["holdout_subjects"])
    subj = pl.get("subject")
    return {**item, "inactivity_observed_s": ev.get("inactivity_observed_s"), "inactivity_required_s": a3["inactivity"]["seconds"], "threshold": ev.get("threshold"),
            "injected": bool(pl.get("injected")), "in_training_set_of_worker_model": (subj not in hold) if subj else None, "in_training_set_of_final_model": True if subj else None,
            "waveform": wave, "alert": alert_out(al) if al else None, "outbox": [{**outbox_out(r), "body": scrub(r.body)} for r in ob]}


# ------------------------------------------------------------------ simulation + agents
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


def pick_fall_window(ctx: Ctx, subject: Optional[str], activity: Optional[str] = None) -> dict:
    import random
    import numpy as np
    from echos.data import sisfall as sf
    hold = ctx.cfg["replay"]["holdout_subjects"]
    if subject and subject not in hold:
        raise ValueError(f"subject must be one of the held-out subjects {hold}")
    if ctx.sisfall_files is None:
        ctx.sisfall_files = sf.list_files()
    fl = ctx.sisfall_files
    fl = fl[fl.subject.isin([subject] if subject else hold) & fl.is_fall]
    if activity:
        fl = fl[fl.activity == activity]
    if fl.empty:
        raise ValueError("no SisFall fall recording available on this machine")
    r = fl.iloc[random.randrange(len(fl))]
    n = int(ctx.cfg["agent3"]["window_s"] * ctx.cfg["agent3"]["fs"])
    a = sf.load_file(r.path)
    return {"file": r.path, "win_start": max(0, int(np.argmax(sf.svm(a))) - n // 2), "subject": r.subject, "activity": r.activity}


def agent_health(ctx: Ctx) -> list[dict]:
    hb_s = ctx.cfg["runtime"]["heartbeat_s"]
    with ctx.eng.begin() as c:
        hbs = {r.agent_id: r for r in c.execute(select(S.agent_heartbeat))}
        rs = {r[0]: r[1] for r in c.execute(select(S.restart_log.c.agent_id, func.count()).group_by(S.restart_log.c.agent_id))}
    out = []
    for a in S.AGENT_KINDS:
        h = hbs.get(a)
        age = None if h is None else max(0.0, time.time() - h.ts)
        out.append({"agent": a, "status": h.status if h else "missing", "alive": bool(h and h.status != "stopped" and age < 3 * hb_s + 10), "heartbeat_age_s": None if age is None else round(age, 1),
                    "last_event_id": h.last_event_id if h else None, "pid": h.pid if h else None, "restarts": int(rs.get(a, 0))})
    return out


# ------------------------------------------------------------------ system
def read_metrics(ctx: Ctx, names: Optional[list[str]]) -> dict:
    avail = sorted(p.stem for p in ctx.metrics_dir.glob("*.json"))
    want = names or [n for n in ("model_selection", "calibration", "fall_audit") if n in avail]
    return {"available": avail, "files": {n: json.loads((ctx.metrics_dir / f"{n}.json").read_text()) for n in want if n in avail}}


def quality(ctx: Ctx) -> dict:
    with ctx.eng.begin() as c:
        fx = [json.loads(r[0]) for r in c.execute(select(S.daily_facts.c.facts))]
    q = [f.get("quality") or {} for f in fx]
    live = {"patient_days": len(fx), "days_unchecked": sum(not x.get("checked", True) for x in q), "sensor_gap_days": sum(bool(x.get("sensor_gap")) for x in q),
            "days_with_activity": sum(bool(f.get("has_activity")) for f in fx), "days_with_vitals": sum(bool(f.get("has_reading")) for f in fx),
            "invalid_values_removed": sum(len(x.get("invalid_values") or []) for x in q)}
    p = ctx.metrics_dir / "data_quality.json"
    return {"live": live, "static": json.loads(p.read_text()) if p.exists() else None}


def llm_budget(ctx: Ctx) -> dict:
    import sqlite3
    lc = ctx.cfg["llm"]
    path = ctx.root / lc.get("cache_path", "data/llm_cache.sqlite")
    rows = []
    if path.exists():
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            rows = c.execute("SELECT ts, event, model, http_status, detail FROM llm_log WHERE ts > ? ORDER BY ts", (time.time() - 86400,)).fetchall()
        except sqlite3.Error:
            rows = []
        c.close()
    now, cool = time.time(), float(lc.get("cooldown_s", 60))
    names = [(lc.get("model"), (lc.get("rpm") or {}).get("primary")), (lc.get("fallback_model"), (lc.get("rpm") or {}).get("fallback"))]
    models = []
    for m, rpm in names:
        if not m:
            continue
        calls = [r for r in rows if r[2] == m and r[1] in ("call", "call_failed")]
        cd = [r[0] for r in rows if r[2] == m and r[1] == "cooldown_start"]
        models.append({"model": m, "rpm_limit": rpm, "effective_rpm_cap": max(1, int(rpm * 0.8)) if rpm else None, "requests_last_minute": sum(r[0] > now - 60 for r in calls),
                       "requests_last_24h": len(calls), "cooldown_remaining_s": round(max(0.0, (cd[-1] + cool - now)) if cd else 0.0, 1),
                       "failures_last_24h": sum(r[1] == "call_failed" for r in calls)})
    hits, reqs = sum(r[1] == "cache_hit" for r in rows), sum(r[1] in ("call", "call_failed") for r in rows)
    return {"models": models, "cache_hits": hits, "model_requests": reqs, "cache_hit_rate": round(hits / (hits + reqs), 3) if hits + reqs else None,
            "recent_events": [{"ts": r[0], "event": r[1], "model": r[2], "http_status": r[3], "detail": scrub(r[4])} for r in rows[-20:]]}


def limitations(ctx: Ctx) -> dict:
    p = ctx.root / "reports" / "RESULTS.md"
    if not p.exists():
        raise NotFound("reports/RESULTS.md not found")
    t = p.read_text()
    m = re.search(r"^## Limitations\s*\n(.*?)(?=^## |\Z|<!--)", t, re.S | re.M)
    if not m:
        raise NotFound("no Limitations section in reports/RESULTS.md")
    return {"source": "reports/RESULTS.md (## Limitations)", "text": m.group(1).strip()}


# ------------------------------------------------------------------ cited sources
def _stored_source(ctx: Ctx, sid: str) -> Optional[dict]:
    with ctx.eng.begin() as c:
        rows = c.execute(select(S.reports.c.provenance).where(S.reports.c.provenance.like(f"%{sid}%"))).fetchall()
    for (pv,) in rows:
        for s in (json.loads(pv).get("sources_retrieved") or []):
            if s.get("id") == sid:
                return s
    return None


def _pubmed_cached(ctx: Ctx, sid: str) -> Optional[dict]:
    import sqlite3
    path = ctx.root / ctx.cfg.get("pubmed", {}).get("cache_path", "data/pubmed_cache.sqlite")
    if not path.exists():
        return None
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        for (v,) in c.execute("SELECT value FROM pubmed_cache"):
            for s in json.loads(v).get("sources", []):
                if s.get("id") == sid:
                    return s
    except sqlite3.Error:
        pass
    finally:
        c.close()
    return None


def _chroma_get(ctx: Ctx, sid: str) -> Optional[dict]:
    """A guideline chunk by id (no embeddings needed: the API never loads a model)."""
    try:
        import chromadb
        from echos.knowledge.ingest import COLLECTION, rag_dir
        r = chromadb.PersistentClient(path=str(rag_dir())).get_collection(COLLECTION).get(ids=[sid])
    except Exception:  # noqa: BLE001 - index missing
        return None
    if not r["ids"]:
        return None
    m = r["metadatas"][0]
    pg = m["page_start"] if m["page_start"] == m["page_end"] else f"{m['page_start']}-{m['page_end']}"
    return {"title": m["title"], "publisher": m["source"], "page": str(pg), "text": r["documents"][0], "file": m.get("file", "")}


def source_detail(ctx: Ctx, sid: str) -> dict:
    """Title, publisher/journal, year, page, link and a SHORT excerpt for a cited source ([KB-n] local, [PM-id] PubMed). PubMed: no abstract text."""
    from echos.reports.retrieval import excerpt
    if sid.startswith("PM-"):
        s = _pubmed_cached(ctx, sid) or _stored_source(ctx, sid)
        if s is None:
            raise NotFound(f"unknown source {sid!r}")
        return {"id": sid, "origin": "pubmed", "title": s.get("title", ""), "source": "PubMed", "year": s.get("year") or None, "page": None,
                "journal": s.get("journal") or (s.get("source") or "").split(",")[0] or None, "url": s.get("url") or f"https://pubmed.ncbi.nlm.nih.gov/{sid[3:]}/", "excerpt": None}
    if sid.startswith("KB-"):
        s = _chroma_get(ctx, sid)
        if s is not None:
            y = re.search(r"\b(19|20)\d{2}\b", f"{s['publisher']} {s['title']} {s['file']}")
            return {"id": sid, "origin": "local", "title": s["title"], "source": s["publisher"], "year": y.group(0) if y else None, "page": s["page"], "journal": None,
                    "url": None, "excerpt": excerpt(s["text"])}
        st = _stored_source(ctx, sid)
        if st is not None:
            return {"id": sid, "origin": "local", "title": st.get("title", ""), "source": st.get("publisher") or st.get("source", ""), "year": st.get("year"), "page": st.get("page"),
                    "journal": None, "url": None, "excerpt": st.get("excerpt")}
    raise NotFound(f"unknown source {sid!r}")
