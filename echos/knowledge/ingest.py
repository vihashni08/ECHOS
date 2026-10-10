"""Ingest guideline documents (PDF / HTML / txt in echos/knowledge/raw/) into a persistent local Chroma collection.

Clean -> split into ~250-word chunks with overlap -> embed with sentence-transformers all-MiniLM-L6-v2 (local, no API key)
-> store with metadata (doc id, title, source name, page range). Chunk ids are stable: KB-1, KB-2, ... in sorted file order.
Usage: PYTHONPATH=. python3 -m echos.knowledge.ingest
"""
from __future__ import annotations

import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

from echos.config import ROOT, load_config

RAW = ROOT / "echos" / "knowledge" / "raw"
COLLECTION = "echos_guidelines"
# filename prefix -> (short doc id, source name). Title comes from the document itself.
SOURCES = {"STEADI": ("STEADI", "CDC STEADI"), "falls-assessment": ("NICE-NG249", "NICE NG249 (2025)"),
           "dementia-assessment": ("NICE-NG97", "NICE NG97 (2018)"), "hypertension-in-adults": ("NICE-NG136", "NICE NG136 (2019)"),
           "news2": ("RCP-NEWS2", "Royal College of Physicians NEWS2 (2017)")}
# doc id -> title, where the PDF's own metadata title is unusable
TITLE_OVERRIDES = {"RCP-NEWS2": "National Early Warning Score (NEWS) 2, Royal College of Physicians (2017)"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        self.skip += tag in ("script", "style", "nav", "footer")
        if tag in ("p", "br", "li", "h1", "h2", "h3", "div", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        self.skip -= tag in ("script", "style", "nav", "footer") and self.skip > 0

    def handle_data(self, d):
        if not self.skip:
            self.parts.append(d)


def read_pages(path: Path) -> tuple[str, list[str]]:
    """Return (title, [page texts]). Plain text / HTML count as a single page."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        r = PdfReader(str(path))
        title = (r.metadata.title if r.metadata and r.metadata.title else path.stem)
        return title.strip(), [p.extract_text() or "" for p in r.pages]
    raw = path.read_text(errors="ignore")
    if ext in (".html", ".htm"):
        t = _Text()
        t.feed(raw)
        m = re.search(r"<title>(.*?)</title>", raw, re.S | re.I)
        return (m.group(1).strip() if m else path.stem), ["".join(t.parts)]
    return path.stem, [raw]


def clean_pages(pages: list[str]) -> list[str]:
    """Join hyphenated line breaks, drop page numbers and running headers/footers (lines repeated on many pages)."""
    lines = [[ln.strip() for ln in p.splitlines() if ln.strip()] for p in pages]
    freq = Counter(ln for pl in lines for ln in set(pl))
    n = len(pages)
    out = []
    for pl in lines:
        keep = [ln for ln in pl if not re.fullmatch(r"(page )?\d{1,3}( of \d+)?", ln.lower())
                and not (n >= 4 and freq[ln] > 0.3 * n and len(ln) < 120)]
        t = re.sub(r"(\w)-\n(\w)", r"\1\2", "\n".join(keep))
        out.append(re.sub(r"\s+", " ", t).strip())
    return out


def chunk_words(pages: list[str], size: int = 250, overlap: int = 50) -> list[dict]:
    """Split into ~size-word chunks with `overlap` words shared; each chunk records its first/last page (1-based)."""
    words = [(w, i + 1) for i, p in enumerate(pages) for w in p.split()]
    out, step, i = [], size - overlap, 0
    while i < len(words):
        win = words[i:i + size]
        if len(win) < size // 4 and out:  # fold a short tail into the previous chunk's neighbourhood: skip it
            break
        out.append({"text": " ".join(w for w, _ in win), "page_start": win[0][1], "page_end": win[-1][1]})
        i += step
    return out


def usable(text: str) -> bool:
    """Drop reference lists / tables of contents / mostly numeric chunks."""
    w = text.split()
    if len(w) < 40:
        return False
    digits = sum(c.isdigit() for c in text) / max(1, len(text))
    return not (text.count("et al") > 4 or digits > 0.2 or text.count("....") > 2 or text.lower().count("doi") > 3)


def build_chunks() -> list[dict]:
    chunks = []
    for path in sorted(p for p in RAW.iterdir() if p.suffix.lower() in (".pdf", ".html", ".htm", ".txt")):
        doc_id, source = next((v for k, v in SOURCES.items() if path.name.startswith(k)), (path.stem[:24], path.stem))
        title, pages = read_pages(path)
        title = TITLE_OVERRIDES.get(doc_id, title)
        for c in chunk_words(clean_pages(pages)):
            if usable(c["text"]):
                chunks.append({**c, "doc_id": doc_id, "title": title, "source": source, "file": path.name})
    for i, c in enumerate(chunks, 1):
        c["id"] = f"KB-{i}"
    return chunks


def get_model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(load_config()["rag"]["embedding_model"])


def rag_dir() -> Path:
    return ROOT / load_config()["rag"]["dir"]


def ingest(rebuild: bool = True) -> int:
    import chromadb
    chunks = build_chunks()
    if not chunks:
        raise SystemExit(f"no documents in {RAW}")
    emb = get_model().encode([c["text"] for c in chunks], normalize_embeddings=True, show_progress_bar=False).tolist()
    rag_dir().mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(rag_dir()))
    if rebuild:
        try:
            client.delete_collection(COLLECTION)
        except Exception:
            pass
    col = client.get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})
    col.add(ids=[c["id"] for c in chunks], embeddings=emb, documents=[c["text"] for c in chunks],
            metadatas=[{k: c[k] for k in ("doc_id", "title", "source", "file", "page_start", "page_end")} for c in chunks])
    return len(chunks)


if __name__ == "__main__":
    n = ingest()
    ch = build_chunks()
    print(f"{n} chunks from {len({c['doc_id'] for c in ch})} documents -> {rag_dir()}")
