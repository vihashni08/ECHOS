"""Live PubMed through NCBI E-utilities (esearch + efetch, abstracts only) for MONITOR / URGENT daily reports.

* Query is built from the structured finding (never from free text): e.g. fall + post-fall inactivity ->
  "older adults AND falls AND post-fall inactivity". First tried restricted to reviews / guidelines / meta-analyses (publication-type filter),
  then unfiltered if that returns nothing. At most `max_results` (3) abstracts.
* At most 3 requests per second (min spacing between requests), 10 s timeout per request, tool name "echos",
  email from ECHOS_NCBI_EMAIL, optional NCBI_API_KEY (both from the environment; never logged).
* Results (including empty ones) are cached in SQLite keyed by the query. Any failure raises PubMedError(reason): the caller falls back to
  local sources and records the reason. Citations: [PM-<pmid>] with title, journal, year and a PubMed link.
Never used on the alert path. Tests mock `http_get`; nothing here is called in tests against the real service."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable, Optional
from urllib import parse, request
from urllib.error import HTTPError

from echos.config import ROOT, load_config

log = logging.getLogger("echos.pubmed")
ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
TOOL = "echos"
PT_FILTER = '(review[pt] OR guideline[pt] OR "practice guideline"[pt] OR meta-analysis[pt] OR "systematic review"[pt])'

# finding type -> PubMed terms (AND-joined). Types without terms (data_gap ...) are never searched.
TERMS = {
    "fall": ["older adults", "falls"],
    "fall_inactivity": ["older adults", "falls", "post-fall inactivity"],
    "blood_pressure": ["older adults", "home blood pressure monitoring", "blood pressure variability"],
    "vitals_news2": ["older adults", "early warning score", "vital signs"],
    "agitation": ["dementia", "agitation", "night-time"],
    "routine_change": ["dementia", "daily activity pattern", "sensor monitoring"],
}


class PubMedError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def build_query(finding_type: str, details: Optional[dict] = None) -> Optional[str]:
    terms = list(TERMS.get(finding_type, []))
    if not terms:
        return None
    d = details or {}
    if d.get("kind") == "critical":
        terms.append("hypotension OR hypertension")
    return " AND ".join(f"({t})" if " OR " in t else t for t in terms)


def _http_get(url: str, params: dict, timeout: float) -> bytes:
    req = request.Request(url + "?" + parse.urlencode(params), headers={"User-Agent": f"{TOOL}/1.0"})
    with request.urlopen(req, timeout=timeout) as r:     # noqa: S310 - fixed https NCBI endpoints
        return r.read()


def _ids(xml: bytes) -> list[str]:
    try:
        return [e.text for e in ET.fromstring(xml).findall(".//IdList/Id") if e.text]
    except ET.ParseError as ex:
        raise PubMedError("esearch: malformed XML") from ex


def _txt(e) -> str:
    return "".join(e.itertext()).strip() if e is not None else ""


def parse_articles(xml: bytes) -> list[dict]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as ex:
        raise PubMedError("efetch: malformed XML") from ex
    out = []
    for a in root.findall(".//PubmedArticle"):
        pmid = _txt(a.find(".//MedlineCitation/PMID"))
        parts = []
        for ab in a.findall(".//Abstract/AbstractText"):
            t = _txt(ab)
            if t:
                parts.append(f"{ab.get('Label')}: {t}" if ab.get("Label") else t)
        if not pmid or not parts:       # abstracts only
            continue
        year = _txt(a.find(".//JournalIssue/PubDate/Year")) or _txt(a.find(".//PubDate/MedlineDate"))[:4]
        out.append({"id": f"PM-{pmid}", "pmid": pmid, "title": _txt(a.find(".//ArticleTitle")), "text": " ".join(parts),
                    "journal": _txt(a.find(".//Journal/Title")) or _txt(a.find(".//Journal/ISOAbbreviation")), "year": year,
                    "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", "origin": "pubmed"})
    return out


class PubMedClient:
    def __init__(self, cfg: Optional[dict] = None, http_get: Optional[Callable] = None, cache_path=None,
                 clock: Callable = time.monotonic, sleep: Callable = time.sleep):
        pc = (cfg or load_config()).get("pubmed") or {}
        self.enabled = bool(pc.get("enabled", True))
        self.max_results, self.timeout = int(pc.get("max_results", 3)), float(pc.get("timeout_s", 10))
        self.gap = 1.0 / float(pc.get("max_rps", 3))
        self.http_get = http_get or _http_get
        self.clock, self.sleep, self._last, self._lock = clock, sleep, None, threading.Lock()
        self.cache_path = str(cache_path or ROOT / pc.get("cache_path", "data/pubmed_cache.sqlite"))
        Path(self.cache_path).parent.mkdir(parents=True, exist_ok=True)
        self._sql("CREATE TABLE IF NOT EXISTS pubmed_cache (key TEXT PRIMARY KEY, query TEXT, value TEXT, ts REAL)")
        self.requests = 0

    def _sql(self, sql, args=()):
        c = sqlite3.connect(self.cache_path, timeout=30)
        try:
            rows = c.execute(sql, args).fetchall()
            c.commit()
            return rows
        finally:
            c.close()

    def _base(self) -> dict:
        p = {"tool": TOOL}
        if os.environ.get("ECHOS_NCBI_EMAIL"):
            p["email"] = os.environ["ECHOS_NCBI_EMAIL"]
        if os.environ.get("NCBI_API_KEY"):
            p["api_key"] = os.environ["NCBI_API_KEY"]
        return p

    def _get(self, url: str, params: dict) -> bytes:
        with self._lock:           # spacing: at most max_rps requests per second, across threads
            now = self.clock()
            if self._last is not None and now - self._last < self.gap:
                self.sleep(self.gap - (now - self._last))
            self._last = self.clock()
        self.requests += 1
        try:
            return self.http_get(url, {**self._base(), **params}, self.timeout)
        except HTTPError as ex:
            raise PubMedError(f"HTTP {ex.code}") from ex
        except TimeoutError as ex:
            raise PubMedError("timeout") from ex
        except PubMedError:
            raise
        except Exception as ex:   # noqa: BLE001 - network down, DNS, TLS ...
            raise PubMedError(f"{type(ex).__name__}") from ex

    def _esearch(self, term: str) -> list[str]:
        return _ids(self._get(ESEARCH, {"db": "pubmed", "term": term, "retmax": self.max_results, "retmode": "xml", "sort": "relevance"}))

    def search(self, finding_type: str, details: Optional[dict] = None) -> dict:
        """-> {"query", "filtered", "cache_hit", "requests", "sources": [...]}; raises PubMedError(reason) on any failure."""
        if not self.enabled:
            raise PubMedError("disabled in config")
        q = build_query(finding_type, details)
        if q is None:
            return {"query": None, "filtered": False, "cache_hit": False, "requests": 0, "sources": []}
        key = hashlib.sha256(f"{q}|{self.max_results}".encode()).hexdigest()
        hit = self._sql("SELECT value FROM pubmed_cache WHERE key=?", (key,))
        if hit:
            v = json.loads(hit[0][0])
            return {**v, "cache_hit": True, "requests": 0}
        n0, filtered = self.requests, True
        ids = self._esearch(f"({q}) AND {PT_FILTER}")
        if not ids:
            filtered, ids = False, self._esearch(q)
        sources = []
        if ids:
            xml = self._get(EFETCH, {"db": "pubmed", "id": ",".join(ids[:self.max_results]), "rettype": "abstract", "retmode": "xml"})
            sources = [{**s, "finding_type": finding_type} for s in parse_articles(xml)][:self.max_results]
        out = {"query": q, "filtered": filtered and bool(ids), "sources": sources}
        self._sql("INSERT OR REPLACE INTO pubmed_cache VALUES (?,?,?,?)", (key, q, json.dumps(out), time.time()))
        return {**out, "cache_hit": False, "requests": self.requests - n0}
