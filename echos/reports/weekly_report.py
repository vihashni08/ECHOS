"""Weekly clinician report pipeline: data -> retrieval (only if the week has a Monitor/Urgent day) -> narrative -> PDF -> email to the CLINICIAN only.
Steps run in this order; the orchestrator calls make_weekly() from a background worker and swallows any failure (a failed weekly report never
breaks a day). `send_weekly_now` is the on-demand entry point for an API / dashboard button."""
from __future__ import annotations

import logging
import pathlib
from html import escape
from typing import Optional


from echos.config import ROOT, load_config
from echos.orchestrator import state as S
from echos.reports import evidence as EV
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


def llm_rewriter(budget, d: dict, sources: list):
    """Rewords the fixed 'Considerations' bullets through the budget manager (cache -> primary -> fallback -> template). Output goes through
    evidence.validate_considerations in evidence.reword, so a bad rewrite is discarded."""
    import json as _json

    from echos.reports.llm_budget import GenerationFailed, make_key

    def rewrite(bullets: list) -> list:
        ids = {s["id"] for s in sources}
        ask = ('Reword each bullet in plain, non-prescriptive language for a clinician (one sentence each). Keep the same "finding" and the same "cites". Never add diagnoses, '
               'medicines or numbers, and keep every number and date exactly as written. Reply with JSON only: {"bullets":[{"finding":str,"text":str,"cites":[ids]}]}.\nBULLETS:\n' + _json.dumps(bullets) + "\nVALID IDS: " + _json.dumps(sorted(ids)))

        def attempt(ctx):
            t = ctx.step("You reword short clinical-review bullets. Reply with JSON only.", [{"role": "user", "text": ask}], None).text.strip()
            if t.startswith("```"):
                t = t.strip("`").removeprefix("json").strip()
            return {"bullets": _json.loads(t)["bullets"]}
        res = budget.run(attempt, key=make_key("weekly-considerations-v1", {"b": bullets, "ids": sorted(ids)}), prompt_version="weekly-considerations-v1")
        if not res.ok:
            raise GenerationFailed(res.fallback_reason or "no result")
        return res.value["bullets"]
    return rewrite


def latest_established_week(eng, patient_id: str, pdy=None, phys=None, demo=None) -> Optional[str]:
    """The most recent ISO week of this patient's processed days in which the vitals baselines (systolic / diastolic) are established
    (falls back to the most recent week). Used by the demo defaults and scripts/send_test_weekly.py."""
    from sqlalchemy import select
    with eng.begin() as c:
        days = [r[0] for r in c.execute(select(S.case_state.c.day).where(S.case_state.c.patient_id == patient_id))]
    weeks = sorted({WD.iso_week(x) for x in days}, reverse=True)
    for w in weeks:
        dd = WD.build_weekly_data(eng, patient_id, w, pdy=pdy, phys=phys, demo=demo)
        if any(dd["vitals_summary"][k]["baseline_established"] for k in ("sys", "dia")):
            return w
    return weeks[0] if weeks else None


def make_weekly(eng, patient_id: str, week, dispatcher=None, budget=None, retriever=None, use_llm: bool = False, root=None, force: bool = False,
                pdy=None, phys=None, demo=None, send: bool = True) -> dict:
    """Build data, narrative, PDF, then queue the email (unless send=False). Raises if the data or PDF step fails."""
    cfg = load_config()
    root = pathlib.Path(root) if root else ROOT
    d = WD.build_weekly_data(eng, patient_id, week, pdy=pdy, phys=phys, demo=demo)
    status = "URGENT" if d["status_counts"]["URGENT"] else "MONITOR"
    findings, sources, notes = EV.gather_findings(d, retriever, status)
    findings = EV.drop_mismatched(findings)
    sources = [s for s in sources if any(s["id"] in f["sources"] for f in findings)]
    cons = EV.template_considerations(findings, d)
    rw = llm_rewriter(budget, d, sources) if (use_llm and budget is not None and cons) else None
    cons, cons_v = EV.reword(findings, cons, rw, {s["id"] for s in sources})
    nar = WN.make_narrative(d, sources, budget=budget, use_llm=use_llm)
    out = root / cfg["weekly"]["dir"] / f"{patient_id}_{d['week']}.pdf"
    pdf = WP.build_pdf(d, nar["text"], sources, out, max_pages=cfg["weekly"].get("max_pages", 3), findings=findings, considerations=cons)
    prov = {**nar["provenance"], "sources_retrieved": [{k: s.get(k) for k in ("id", "origin", "title", "source", "publisher", "year", "page", "journal", "url", "excerpt")} for s in sources],
            "retrieval_events": notes, "findings": [{k: f[k] for k in ("key", "label", "n", "sources")} for f in findings],
            "vitals_baseline_established": any(d["vitals_summary"][k]["baseline_established"] for k in ("sys", "dia")),
            "considerations": cons, "considerations_validation": cons_v, "narrative": nar["text"], "pdf": {"path": str(out), "pages": pdf["pages"], "bytes": pdf["bytes"]}}
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
