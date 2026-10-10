"""Retrieve guideline chunks for a structured finding. Local embeddings, no API key. Returns [] if RAG is disabled,
not ingested, or fails, so the report agent keeps working without it."""
from __future__ import annotations

import json
import logging
import time

from echos.config import load_config
from echos.knowledge.ingest import COLLECTION, get_model, rag_dir

LOG = logging.getLogger("echos.rag")
# finding type -> natural-language query. Types with no query (e.g. data_gap) get no guidance.
QUERIES = {
    "fall": "older adult fall: assessment and prevention after a fall",
    "fall_inactivity": "older adult fall followed by inactivity, long lie on the floor, checking and getting help after a fall",
    "blood_pressure": "elevated systolic blood pressure above personal baseline in an older adult, home blood pressure monitoring",
    "vitals_news2": "abnormal heart rate and blood pressure, early warning score, clinical deterioration",
    "agitation": "night-time restlessness and agitation in dementia, non-pharmacological management",
    "routine_change": "change in daily routine and sleep-wake pattern in a person living with dementia",
}
_state: dict = {}


def enabled() -> bool:
    return bool((load_config().get("rag") or {}).get("enabled", False))


def build_query(finding_type: str, details: dict | None = None) -> str | None:
    q = QUERIES.get(finding_type)
    if q is None:
        return None
    d = details or {}
    extra = []
    if d.get("kind") == "critical":
        extra.append("very high or very low reading, urgent medical review")
    if d.get("post_fall_inactive"):
        extra.append("person not moving after the fall")
    return q + (". " + "; ".join(extra) if extra else "")


def _col():
    if "col" not in _state:
        import chromadb
        _state["col"] = chromadb.PersistentClient(path=str(rag_dir())).get_collection(COLLECTION)
        _state["model"] = get_model()
    return _state["col"], _state["model"]


def _log(rec: dict):
    LOG.info("retrieval %s", json.dumps(rec))
    try:
        p = rag_dir() / "retrieval_log.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass


def retrieve(finding_type: str, details: dict | None = None, k: int | None = None) -> list[dict]:
    """Top-k chunks: [{id: 'KB-3', text, title, source, doc_id, page, score, finding_type}], best first, above min_score."""
    cfg = load_config()["rag"]
    k = k or cfg["k"]
    if not enabled():
        return []
    q = build_query(finding_type, details)
    if q is None:
        return []
    try:
        col, model = _col()
        r = col.query(query_embeddings=model.encode([q], normalize_embeddings=True).tolist(), n_results=k)
    except Exception as ex:  # not ingested / chroma error: report agent continues without guidance
        LOG.warning("retrieval failed (%s): %s", type(ex).__name__, ex)
        return []
    out = []
    for cid, doc, md, dist in zip(r["ids"][0], r["documents"][0], r["metadatas"][0], r["distances"][0]):
        score = round(1 - dist, 3)
        if score < cfg["min_score"]:
            continue
        pg = md["page_start"] if md["page_start"] == md["page_end"] else f"{md['page_start']}-{md['page_end']}"
        out.append({"id": cid, "text": doc, "title": md["title"], "source": md["source"], "doc_id": md["doc_id"],
                    "page": pg, "score": score, "finding_type": finding_type})
    _log({"ts": round(time.time(), 1), "finding_type": finding_type, "query": q, "k": k, "ids": [c["id"] for c in out],
          "scores": [c["score"] for c in out]})
    return out
