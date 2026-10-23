"""Evidence agent. Goal: every URGENT alert and every assigned review carries an explanation that can be traced to a guideline passage, or says plainly
that no supporting source was found.

Belief: the guideline index (echos/evidence/corpus.py) and a cache of earlier Gemini answers (data/knowledge/llm_cache.sqlite, so a replayed day makes no second call).
Input: EVIDENCE_REQUEST messages from the Care agent (posted AFTER the alert is queued, so evidence never delays an URGENT alert).
Decision: which passages to attach and whether the generated text passes the validator; otherwise the fixed template is used.
Output: a row in evidence_card (read by the API / UI) and an EVIDENCE_READY broadcast. It never changes a risk, a review decision or an alert."""
from __future__ import annotations

import json
import logging
import sqlite3
import time

from sqlalchemy import select

from echos.config import ROOT, load_config
from echos.evidence import corpus
from echos.evidence.explain import build_card
from echos.evidence.llm import make_llm
from echos.orchestrator import state as S
from echos.team import bus
from echos.team.agent_base import AgentBase

log = logging.getLogger("echos.evidence")
AGENT = "evidence"


class EvidenceAgent(AgentBase):
    AGENT = AGENT
    KINDS = ()

    def __init__(self, eng, retriever=None, llm="auto", cfg: dict | None = None, **kw):
        super().__init__(eng, **kw)
        self.cfg = cfg or load_config().get("evidence") or {}
        self.retriever = retriever if retriever is not None else corpus.get_retriever()
        self.llm = make_llm() if llm == "auto" else llm

    # Gemini answers are cached in their own file, NOT in the demo database: a demo reset (a fresh database) keeps them, so a second run makes no API call.
    def _cache(self) -> sqlite3.Connection:
        path = ROOT / "data" / "knowledge" / "llm_cache.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path, timeout=30)
        con.execute("CREATE TABLE IF NOT EXISTS llm_cache (key TEXT PRIMARY KEY, response TEXT, model TEXT, ts REAL)")
        return con

    def _cache_get(self, key: str):
        con = self._cache()
        try:
            r = con.execute("SELECT response FROM llm_cache WHERE key = ?", (key,)).fetchone()
        finally:
            con.close()
        return r[0] if r else None

    def _cache_put(self, key: str, text: str, model: str) -> None:
        con = self._cache()
        try:
            with con:
                con.execute("INSERT OR REPLACE INTO llm_cache VALUES (?, ?, ?, ?)", (key, text, model, time.time()))
        finally:
            con.close()

    def make_card(self, m: dict) -> dict:
        pl = m["payload"]
        facts = {"kind": pl.get("kind"), "reasons": pl.get("reasons") or [], "vitals": pl.get("vitals")}
        return build_card(facts, self.retriever, self.llm, int(self.cfg.get("k", 3)), float(self.cfg.get("min_score", 0.08)),
                          cache=(self._cache_get, self._cache_put))

    def handle(self, m: dict) -> None:
        pl = m["payload"]
        card_id = f"{pl.get('kind')}|{m['patient_id']}|{m['day']}|{pl.get('ref', '')}"
        with self.eng.begin() as c:
            if c.execute(select(S.evidence_card.c.card_id).where(S.evidence_card.c.card_id == card_id)).first():
                return                                               # idempotent: a restart re-reads messages it already handled
        card = self.make_card(m)
        with self.eng.begin() as c:
            S.insert_ignore(c, S.evidence_card, card_id=card_id, patient_id=m["patient_id"], day=m["day"], kind=pl.get("kind"), ref=pl.get("ref", ""),
                            card=json.dumps(card, default=str), source=card["source"], created_ts=S.now())
        bus.post(self.eng, AGENT, "all", "evidence_ready", m["patient_id"], m["day"], {"card_id": card_id, "source": card["source"]})

    def step(self) -> int:
        """One request per pass, URGENT cards first: with a rate-limited LLM a red-flag case is never queued behind earlier reviews."""
        msgs = bus.poll(self.eng, AGENT, ["evidence_request"], limit=500)
        if not msgs:
            return 0
        m = sorted(msgs, key=lambda x: (x["payload"].get("kind") != "urgent", x["id"]))[0]
        try:
            self.handle(m)
        except Exception:  # noqa: BLE001 - one bad request never stops the agent
            log.exception("evidence request %s failed", m["id"])
        bus.done(self.eng, [m["id"]])
        return 1
