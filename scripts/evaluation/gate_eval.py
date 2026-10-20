"""Gatekeeper evaluation on the stored replay (data/e2e_replay/echos.db from scripts/evaluation/e2e.py). COUNT ONLY: a counting FakeLLM and stub PubMed/guideline
retrieval, no real Gemini / PubMed / SMTP. The orchestrator is re-run over the stored agent outputs twice (gate ON = NORMAL days get a template report;
gate OFF = hypothetical always-LLM policy: every day goes to the LLM) and the two runs are compared: triage results and alerts must be identical.
Writes reports/metrics/gate_eval.json and the block <!-- gate_eval:start/end --> in reports/RESULTS.md.
Usage: PYTHONPATH=. python scripts/evaluation/gate_eval.py"""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import time
import warnings
from collections import Counter
from pathlib import Path
from unittest import mock

warnings.filterwarnings("ignore")
from echos.offline import force_offline  # noqa: E402

force_offline()
from sqlalchemy import delete, select  # noqa: E402

from echos.alerts.dispatcher import AlertDispatcher  # noqa: E402
from echos.alerts.mailer import FakeMailer  # noqa: E402
from echos.config import ROOT  # noqa: E402
from echos.orchestrator import state as S  # noqa: E402
from echos.orchestrator.pipeline import build_pipeline, run_orchestrator  # noqa: E402
from echos.reports.daily import agent as A  # noqa: E402
from echos.reports.llm.fake_llm import FakeLLM  # noqa: E402
from echos.reports.retrieval import GatedRetriever  # noqa: E402

SRC = ROOT / "data/e2e_replay/echos.db"
OUT = ROOT / "reports/metrics/gate_eval.json"
RESULTS = ROOT / "reports/RESULTS.md"
START, END = "<!-- gate_eval:start -->", "<!-- gate_eval:end -->"
TO = {"caregiver": "care@example.test", "clinician": "doc@example.test"}


def script(system, messages, tools):
    """Typical tool-using model: step 1 asks for context + guidance, then a final answer the validator rejects (so every attempt costs 2 requests)."""
    if tools and len(messages) == 1:
        return {"calls": [{"name": "get_last_7_days", "args": {}}, {"name": "retrieve_guidance", "args": {"finding_type": A.FINDING_TYPES[0]}}]}
    return {"text": "{}"}


class StubPubMed:
    def __init__(self):
        self.n = 0

    def search(self, finding_type, details=None):
        self.n += 1
        return {"query": finding_type, "filtered": False, "cache_hit": False, "requests": 1, "sources": []}


def run_policy(gate: bool, tmp: Path) -> dict:
    work = tmp / ("gate_on" if gate else "gate_off")
    work.mkdir()
    c = sqlite3.connect(SRC)
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    c.close()
    shutil.copy(SRC, work / "echos.db")
    eng = S.get_engine(work / "echos.db")
    with eng.begin() as cn:
        for t in (S.case_state, S.alerts, S.outbox, S.reports, S.daily_facts):
            cn.execute(delete(t))
        cn.execute(delete(S.agent_checkpoint).where(S.agent_checkpoint.c.agent_id.like("case:%")))      # per-case progress: a copied finished run would resume as 'already done'
        cn.execute(S.agent_heartbeat.update().values(ts=time.time()))
    fake = FakeLLM([script], model="counting-fake")
    pm, mailer, calls = StubPubMed(), FakeMailer(), Counter()
    orig = GatedRetriever.retrieve

    def counting(self, status, finding_type, details=None):
        calls[f"retrieve_calls_{status}"] += 1
        return orig(self, status, finding_type, details)
    disp = AlertDispatcher(eng, mailer, to=TO)
    with mock.patch.object(GatedRetriever, "retrieve", counting), mock.patch.object(A, "_default_pubmed", lambda: pm), \
            mock.patch.object(A, "_safe_retrieve", lambda ft, d=None, k=None: []):
        graph = build_pipeline(eng, report_llm=fake, gate=gate, weekly_pdf=False, dispatcher=disp, report_root=work / "files")
        n = run_orchestrator(eng, graph, until_done=lambda: True, timeout=500)
        disp.flush(60)
        A.drain_all(500)
    with eng.begin() as cn:
        cases = {(r.patient_id, r.day): (r.status, r.reasons, bool(r.data_gap)) for r in cn.execute(select(S.case_state))}
        alerts = sorted(r.alert_id for r in cn.execute(select(S.alerts)))
        ob = sorted((r.alert_id, r.recipient_role) for r in cn.execute(select(S.outbox)))
        rep = cn.execute(select(S.reports.c.mode, S.reports.c.provenance)).fetchall()
    return {"cases": cases, "alerts": alerts, "outbox": ob, "processed": n, "llm_requests": fake.calls,
            "reports_by_mode": dict(Counter(r.mode for r in rep)), "pubmed_calls": pm.n,
            "retrieval": {k: v for k, v in calls.items()}, "mail_sent": len(mailer.sent),
            "llm_reports_attempted": sum(1 for r in rep if json.loads(r.provenance or "{}").get("requests", 0) > 0 or "LLM" in str(json.loads(r.provenance or "{}").get("fallback_reason")))}


def main():
    tmp = Path(tempfile.mkdtemp())
    try:
        on, off = run_policy(True, tmp), run_policy(False, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    st = Counter(v[0] for v in on["cases"].values())
    nd = len(on["cases"])
    routed_on, routed_off = st["MONITOR"] + st["URGENT"], nd
    ex = lambda r: sum(v for k, v in r["retrieval"].items() if not k.endswith("NORMAL"))   # noqa: E731
    bl = lambda r: r["retrieval"].get("retrieve_calls_NORMAL", 0)   # noqa: E731
    ex_on, ex_off, bl_on, bl_off = ex(on), ex(off), bl(on), bl(off)
    same_cases = on["cases"] == off["cases"]
    missed = sorted(set(off["alerts"]) - set(on["alerts"]))
    res = {"patient_days": nd, "days_by_status": dict(st),
           "reports_routed_to_llm": {"gate_on": routed_on, "always_llm": routed_off, "avoided": routed_off - routed_on,
                                     "avoided_share": round((routed_off - routed_on) / nd, 3)},
           "llm_requests_counting_fake": {"gate_on": on["llm_requests"], "always_llm": off["llm_requests"], "avoided": off["llm_requests"] - on["llm_requests"]},
           "llm_requests_lower_bound_2_per_report": {"gate_on": 2 * routed_on, "always_llm": 2 * routed_off, "avoided": 2 * (routed_off - routed_on)},
           "retrieval_calls": {"gate_on": on["retrieval"], "always_llm": off["retrieval"]},
           "retrievals_executed": {"gate_on": ex_on, "always_llm": ex_off}, "retrieval_requests_blocked_by_guard": {"gate_on": bl_on, "always_llm": bl_off},
           "pubmed_search_calls": {"gate_on": on["pubmed_calls"], "always_llm": off["pubmed_calls"]},
           "triage_identical": same_cases, "alerts_identical": on["alerts"] == off["alerts"], "outbox_identical": on["outbox"] == off["outbox"],
           "alerts_missed_due_to_gating": len(missed), "alerts_gate_on": len(on["alerts"]), "alerts_always_llm": len(off["alerts"]),
           "notes": ["count only: counting FakeLLM, stub PubMed, no real call; the orchestrator was re-run on the stored agent outputs of the replay",
                     "the fake model asks for context + guidance, then returns an answer the validator rejects (retry once): 4 requests per LLM-routed report; "
                     "the 2-per-report lower bound assumes a first-time valid answer after one tool round; a real model may use up to 5 per attempt",
                     "retrieval is ALSO gated by status inside GatedRetriever (NORMAL days never retrieve): an always-LLM policy would still execute the same retrievals, and would attempt %d more that the guard blocks; without that guard they would all execute" % bl_off,
                     "the avoided share depends on the data mix: it is the NORMAL share of patient-days (here %.1f%%); a cohort with more Monitor days avoids less" % (100 * st["NORMAL"] / nd)]}
    OUT.write_text(json.dumps(res, indent=1))
    r = res
    md = [START, "## Gatekeeper evaluation (written by scripts/evaluation/gate_eval.py)", "",
          f"Count only (counting FakeLLM, stub retrieval, nothing sent) on the stored replay: {nd} patient-days "
          f"({st['NORMAL']} Normal, {st['MONITOR']} Monitor, {st['URGENT']} Urgent). Gate ON = NORMAL days get a template report; gate OFF = hypothetical always-LLM policy.", "",
          "| Measure | Gate ON | Always-LLM | Avoided |", "|---|---|---|---|",
          f"| Daily reports routed to the LLM | {routed_on} | {routed_off} | {routed_off - routed_on} ({r['reports_routed_to_llm']['avoided_share']:.1%} of days) |",
          f"| LLM requests, lower bound (2 per report) | {2 * routed_on} | {2 * routed_off} | {2 * (routed_off - routed_on)} |",
          f"| LLM requests, counting fake (4 per report) | {on['llm_requests']} | {off['llm_requests']} | {off['llm_requests'] - on['llm_requests']} |",
          f"| Retrievals executed (local index; MONITOR / URGENT only) | {ex_on} | {ex_off} | {ex_off - ex_on} |",
          f"| Retrieval requests blocked by the retriever's status guard (NORMAL days) | {bl_on} | {bl_off} | n/a: the guard exists in both policies |",
          f"| Live PubMed searches | {on['pubmed_calls']} | {off['pubmed_calls']} | {off['pubmed_calls'] - on['pubmed_calls']} |",
          "", f"Triage results identical with gate on and off: **{same_cases}** ({nd} days); alerts identical: **{r['alerts_identical']}** ({len(on['alerts'])} vs {len(off['alerts'])}); "
          f"outbox rows identical: **{r['outbox_identical']}**; alerts missed due to gating: **{len(missed)}**.", "",
          f"The avoided share is the Normal share of days in this cohort ({100 * st['NORMAL'] / nd:.1f}%); it depends on the data mix and would be smaller where more days are Monitor. "
          "The gate never touches triage or alerts: the gated step runs after the alert has been queued.", END]
    s = RESULTS.read_text()
    block = "\n".join(md)
    s = s[:s.index(START)] + block + s[s.index(END) + len(END):] if START in s else s.rstrip() + "\n\n" + block + "\n"
    RESULTS.write_text(s)
    print(json.dumps({k: res[k] for k in ("patient_days", "reports_routed_to_llm", "llm_requests_counting_fake", "llm_requests_lower_bound_2_per_report", "retrievals_executed", "retrieval_requests_blocked_by_guard",
                                          "pubmed_search_calls", "triage_identical", "alerts_identical", "outbox_identical", "alerts_missed_due_to_gating")}))


if __name__ == "__main__":
    main()
