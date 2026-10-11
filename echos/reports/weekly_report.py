"""Weekly clinician report pipeline: data -> retrieval (only if the week has a Monitor/Urgent day) -> narrative -> PDF -> email to the CLINICIAN only.
Steps run in this order; the orchestrator calls make_weekly() from a background worker and swallows any failure (a failed weekly report never
breaks a day). `send_weekly_now` is the on-demand entry point for an API / dashboard button."""
from __future__ import annotations

import logging
import pathlib
from html import escape
from typing import Optional

import pandas as pd

from echos.config import ROOT, load_config
from echos.orchestrator import state as S
from echos.reports import weekly_data as WD
from echos.reports import weekly_narrative as WN
from echos.reports import weekly_pdf as WP

log = logging.getLogger("echos.weekly")
MAX_SOURCES = 6


def email_parts(d: dict) -> tuple[str, str, str]:
    """(subject, plain text, html): five short lines."""
    c, comp = d["status_counts"], d["completeness"]
    rng = f"{d['week_start']} to {d['week_end']}"
    lines = [f"Days by status: Normal {c['NORMAL']}, Monitor {c['MONITOR']}, Urgent {c['URGENT']} (data gap {c['DATA_GAP']}).",
             f"Vitals alerts: {len(d['vitals_alerts'])}.",
             f"Falls: {len(d['falls'])}.",
             f"Unusual-pattern days: {len(d['unusual_days'])}.",
             f"Data completeness: vitals on {comp['days_with_vitals']} of 7 days, activity on {comp['days_with_activity']} of 7 days. "
             "Full report attached (decision support, not a diagnosis)."]
    subject = f"[ECHOS Weekly] {d['patient_id']} - {rng}"
    html = "<html><body>" + "".join(f"<p>{escape(x)}</p>" for x in lines) + "</body></html>"
    return subject, "\n".join(lines), html


def gather_sources(d: dict, retriever, status: str) -> tuple[list, list]:
    """Local + PubMed sources for the finding types of the week; ([], note) when the week has no Monitor/Urgent day."""
    if not d["any_monitor_or_urgent"] or retriever is None:
        return [], ["retrieval skipped: no Monitor or Urgent day" if not d["any_monitor_or_urgent"] else "no retriever"]
    out, seen, notes = [], set(), []
    for ft in d["finding_types"]:
        try:
            r = retriever.retrieve(status, ft, {})
        except Exception as ex:   # noqa: BLE001 - references are optional
            notes.append(f"{ft}: retrieval failed ({type(ex).__name__})")
            continue
        notes += [f"{ft}: {e}" for e in r["events"]]
        for s in r["sources"]:
            if s["id"] not in seen and len(out) < MAX_SOURCES:
                seen.add(s["id"])
                out.append(s)
    return out, notes


def make_weekly(eng, patient_id: str, week, dispatcher=None, budget=None, retriever=None, use_llm: bool = False, root=None, force: bool = False,
                pdy=None, phys=None, demo=None, send: bool = True) -> dict:
    """Build data, narrative, PDF, then queue the email (unless send=False). Raises if the data or PDF step fails."""
    cfg = load_config()
    root = pathlib.Path(root) if root else ROOT
    d = WD.build_weekly_data(eng, patient_id, week, pdy=pdy, phys=phys, demo=demo)
    status = "URGENT" if d["status_counts"]["URGENT"] else "MONITOR"
    sources, notes = gather_sources(d, retriever, status)
    nar = WN.make_narrative(d, sources, budget=budget, use_llm=use_llm)
    out = root / cfg["weekly"]["dir"] / f"{patient_id}_{d['week']}.pdf"
    pdf = WP.build_pdf(d, nar["text"], sources, out, max_pages=cfg["weekly"].get("max_pages", 3))
    prov = {**nar["provenance"], "sources_retrieved": [{k: s.get(k) for k in ("id", "origin", "title", "source", "publisher", "year", "page", "journal", "url", "excerpt")} for s in sources],
            "retrieval_events": notes, "narrative": nar["text"], "pdf": {"path": str(out), "pages": pdf["pages"], "bytes": pdf["bytes"]}}
    with eng.begin() as c:
        S.upsert(c, S.reports, ["patient_id", "day", "kind"], patient_id=patient_id, day=d["week_end"], kind="weekly_pdf", mode=nar["mode"], path=str(out),
                 validation=S.j(nar["validation"]), created_ts=S.now(), provenance=S.j(prov))
    queued = None
    if send and dispatcher is not None:
        subject, text, html = email_parts(d)
        queued = dispatcher.dispatch_weekly(patient_id, d["week"], subject, text, html, str(out), d["week_end"], force=force)
    return {"patient_id": patient_id, "week": d["week"], "pdf": pdf, "mode": nar["mode"], "queued_alert_id": queued, "provenance": prov, "data": d}


def send_weekly_now(patient_id: str, week, force: bool = False, eng=None, dispatcher=None, use_llm: bool = True, budget="auto", retriever=None,
                    root=None, **data_kw) -> dict:
    """On-demand 'send now' (API / dashboard). Builds the PDF and emails it to the clinician. Without force a week that was already sent is NOT sent
    again (queued_alert_id None); with force=True a new copy is sent. The LLM narrative is used when a budget exists (use_llm and a configured key)."""
    from echos.alerts.dispatcher import AlertDispatcher
    from echos.alerts.mailer import make_mailer
    from echos.reports.agent import ReportAgent
    from echos.reports.llm_budget import make_budget
    eng = eng or S.get_engine()
    dispatcher = dispatcher or AlertDispatcher(eng, make_mailer())
    budget = make_budget() if budget == "auto" else budget
    retriever = retriever or ReportAgent(eng, None, root=root, budget=budget).retriever
    return make_weekly(eng, patient_id, week, dispatcher, budget=budget, retriever=retriever, use_llm=bool(use_llm and budget), root=root, force=force, **data_kw)
