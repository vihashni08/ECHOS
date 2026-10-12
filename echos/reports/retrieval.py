"""Gated retrieval for the daily report. Only MONITOR and URGENT days may retrieve; a NORMAL day gets nothing and makes NO call (local or PubMed).
Order: local index first (ids like [KB-65]), then live PubMed (ids like [PM-12345678]); if PubMed fails the local sources are used alone and the
failure reason is recorded. Returned sources share one shape: {id, title, source, text, origin, url?, page?, finding_type}."""
from __future__ import annotations

import logging
import re
from typing import Callable, Optional

from echos.knowledge.pubmed import PubMedClient, PubMedError
from echos.knowledge.relevance import keep

log = logging.getLogger("echos.retrieval")
GATED_STATUSES = ("MONITOR", "URGENT")


def excerpt(text: str, n: int = 40) -> str:
    """At most n words (local guideline chunks only; PubMed abstracts are never shown)."""
    w = text.split()
    return " ".join(w[:n]) + (" ..." if len(w) > n else "")


def _norm_local(c: dict) -> dict:
    m = re.search(r"\b(19|20)\d{2}\b", " ".join(str(c.get(k, "")) for k in ("source", "title")))
    return {"id": c["id"], "title": c.get("title", ""), "source": f"{c.get('source', '')}, page {c.get('page', '')}".strip(", "), "publisher": c.get("source", ""),
            "year": m.group(0) if m else None, "page": str(c.get("page", "")), "journal": None, "url": None, "excerpt": excerpt(c["text"]),
            "text": c["text"], "origin": "local", "finding_type": c.get("finding_type"), "score": c.get("score")}


def _norm_pm(s: dict) -> dict:
    return {"id": s["id"], "title": s["title"], "source": f"{s['journal']}, {s['year']}".strip(", "), "publisher": "PubMed", "year": s.get("year") or None, "page": None,
            "journal": s.get("journal"), "url": s["url"], "excerpt": None, "text": s["text"], "origin": "pubmed", "finding_type": s.get("finding_type")}


class GatedRetriever:
    def __init__(self, local: Callable, pubmed: Optional[PubMedClient] = None, pubmed_factory: Optional[Callable] = None):
        self.local, self._pm, self._factory = local, pubmed, pubmed_factory
        self.calls = {"local": 0, "pubmed": 0}      # counted for tests / provenance

    @property
    def pubmed(self) -> Optional[PubMedClient]:
        if self._pm is None and self._factory:
            self._pm = self._factory()
        return self._pm

    def retrieve(self, status: str, finding_type: str, details: Optional[dict] = None) -> dict:
        """-> {"sources": [...], "events": [str], "pubmed_failure": str | None}"""
        if status not in GATED_STATUSES:
            return {"sources": [], "events": [f"retrieval gated off: status {status}"], "pubmed_failure": None}
        events, sources, failure = [], [], None
        self.calls["local"] += 1
        try:
            loc = [_norm_local(c) for c in (self.local(finding_type, details) or [])]
        except Exception as ex:   # noqa: BLE001 - local index down: continue with PubMed only
            loc, _ = [], events.append(f"local index failed ({type(ex).__name__})")
        sources += loc
        events.append(f"local: {len(loc)} source(s)")
        pm = self.pubmed
        if pm is not None:
            self.calls["pubmed"] += 1
            try:
                r = pm.search(finding_type, details)
                sources += [_norm_pm(s) for s in r["sources"]]
                events.append(f"pubmed: {len(r['sources'])} source(s)" + (" (cache)" if r["cache_hit"] else f" ({r['requests']} request(s)"
                                                                          + (", review/guideline filter)" if r["filtered"] else ", unfiltered)")))
            except PubMedError as ex:
                failure = ex.reason
                events.append(f"pubmed failed ({ex.reason}); local sources only")
                log.warning("PubMed failed (%s): using local sources only", ex.reason)
        kept = [s for s in sources if keep(finding_type, s)]
        if len(kept) < len(sources):
            events.append(f"{len(sources) - len(kept)} source(s) dropped: not relevant to {finding_type}")
        sources = kept
        seen, uniq = set(), []
        for s in sources:
            if s["id"] not in seen:
                seen.add(s["id"])
                uniq.append(s)
        return {"sources": uniq, "events": events, "pubmed_failure": failure}
