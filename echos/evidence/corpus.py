"""Guideline corpus and retrieval for the Evidence agent.

Documents (PDF / txt / html) live in data/knowledge/raw/ (git-ignored: publisher material). `build_index()` cleans them, splits every page into ~150-word
chunks with a small overlap, and fits a TF-IDF index (unigrams + bigrams, scikit-learn). Retrieval is the cosine between the query and each chunk: local,
deterministic, no API and no embedding model. Chunk ids are stable: <DOC>-p<page>-<n>, so a citation can always be checked against the stored text.

    PYTHONPATH=. python scripts/build_knowledge.py
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer

from echos.config import ROOT

RAW = ROOT / "data" / "knowledge" / "raw"
INDEX = ROOT / "data" / "knowledge" / "index.joblib"
# file stem -> (source name shown to the user, link). Any other file dropped into RAW is indexed under its own name.
SOURCES = {
    "NICE-NG136": ("NICE NG136: Hypertension in adults (2019)", "https://www.nice.org.uk/guidance/ng136"),
    "RCP-NEWS2": ("Royal College of Physicians: National Early Warning Score 2 (2017)", "https://www.rcp.ac.uk/improving-care/resources/national-early-warning-score-news-2/"),
    "NICE-NG97": ("NICE NG97: Dementia, assessment, management and support (2018)", "https://www.nice.org.uk/guidance/ng97"),
}
CHUNK_WORDS, OVERLAP = 150, 30


@dataclass
class Passage:
    id: str
    doc: str
    source: str
    url: str
    page: int
    text: str
    score: float = 0.0

    def to_dict(self) -> dict:
        return {"id": self.id, "doc": self.doc, "source": self.source, "url": self.url, "page": self.page, "text": self.text, "score": round(self.score, 4)}


def _pages(path: Path) -> list[str]:
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        return [p.extract_text() or "" for p in PdfReader(str(path)).pages]
    return [path.read_text(errors="ignore")]


def _clean(t: str) -> str:
    t = re.sub(r"(\w)-\n(\w)", r"\1\2", t)
    return re.sub(r"\s+", " ", t).strip()


def chunk_document(doc: str, pages: list[str]) -> list[dict]:
    name, url = SOURCES.get(doc, (doc, ""))
    out = []
    for pno, raw in enumerate(pages, 1):
        words = _clean(raw).split()
        step = CHUNK_WORDS - OVERLAP
        for n, i in enumerate(range(0, max(len(words), 1), step)):
            piece = words[i:i + CHUNK_WORDS]
            if len(piece) < 40:
                continue
            out.append({"id": f"{doc}-p{pno}-{n}", "doc": doc, "source": name, "url": url, "page": pno, "text": " ".join(piece)})
    return out


def build_index(raw: Path = RAW, index: Path = INDEX) -> dict:
    chunks = []
    for p in sorted(raw.glob("*")):
        if p.suffix.lower() in (".pdf", ".txt", ".html", ".htm", ".md"):
            chunks += chunk_document(p.stem, _pages(p))
    if not chunks:
        raise FileNotFoundError(f"no guideline documents in {raw}")
    vec = TfidfVectorizer(ngram_range=(1, 2), stop_words="english", sublinear_tf=True, min_df=1)
    mat = vec.fit_transform([c["text"] for c in chunks])
    index.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"chunks": chunks, "vec": vec, "mat": mat}, index)
    docs = sorted({c["doc"] for c in chunks})
    return {"documents": docs, "chunks": len(chunks)}


class Retriever:
    def __init__(self, index: Path = INDEX):
        d = joblib.load(index)
        self.chunks, self.vec, self.mat = d["chunks"], d["vec"], d["mat"]
        self.by_id = {c["id"]: c for c in self.chunks}

    def search(self, query: str, k: int = 3, min_score: float = 0.08, docs: set | None = None) -> list[Passage]:
        if not query.strip():
            return []
        sims = (self.mat @ self.vec.transform([query]).T).toarray().ravel()
        order = sims.argsort()[::-1]
        out = []
        for i in order:
            c = self.chunks[i]
            if sims[i] < min_score or len(out) >= k:
                break
            if docs and c["doc"] not in docs:
                continue
            out.append(Passage(score=float(sims[i]), **c))
        return out

    def get(self, pid: str) -> dict | None:
        return self.by_id.get(pid)


_cached: Retriever | None = None


def get_retriever() -> Retriever | None:
    """The shared retriever, or None when the index has not been built (the agent then writes 'no supporting source found')."""
    global _cached
    if _cached is None and INDEX.exists():
        _cached = Retriever()
    return _cached
