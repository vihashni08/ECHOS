import pytest

from echos.knowledge import ingest, retrieve as R
from echos.reports.validator import validate_report

DOC = ("Older adults who have fallen should be offered a multifactorial falls risk assessment including medication review, "
       "vision, footwear, home hazards and blood pressure. ") * 12
BP = ("Measure blood pressure at home twice daily for seven days and use the average to confirm hypertension in adults. "
      "Offer lifestyle advice and review treatment targets for older adults. ") * 12


def test_chunking_and_metadata():
    pages = [DOC, BP]
    ch = ingest.chunk_words(ingest.clean_pages(pages), size=100, overlap=20)
    assert len(ch) > 3 and all(len(c["text"].split()) <= 100 for c in ch)
    assert ch[0]["page_start"] == 1 and ch[-1]["page_end"] == 2
    assert ch[0]["text"].split()[80:] == ch[1]["text"].split()[:20]  # overlap
    real = ingest.build_chunks()
    assert real and real[0]["id"] == "KB-1"
    assert {"doc_id", "title", "source", "page_start", "page_end", "file", "text"} <= set(real[0])
    assert all(40 <= len(c["text"].split()) <= 300 for c in real)


def test_html_and_txt_are_read(tmp_path):
    (tmp_path / "a.html").write_text("<html><title>T1</title><script>x=1</script><body><p>Hello falls</p></body></html>")
    (tmp_path / "b.txt").write_text("plain text")
    assert ingest.read_pages(tmp_path / "a.html")[0] == "T1"
    assert "Hello falls" in ingest.read_pages(tmp_path / "a.html")[1][0] and "x=1" not in ingest.read_pages(tmp_path / "a.html")[1][0]
    assert ingest.read_pages(tmp_path / "b.txt")[1] == ["plain text"]


@pytest.fixture()
def small_index(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "falls.txt").write_text(DOC)
    (raw / "bp.txt").write_text(BP)
    monkeypatch.setattr(ingest, "RAW", raw)
    monkeypatch.setattr(ingest, "rag_dir", lambda: tmp_path / "rag")
    monkeypatch.setattr(R, "rag_dir", lambda: tmp_path / "rag")
    monkeypatch.setattr(R, "enabled", lambda: True)
    R._state.clear()
    assert ingest.ingest() >= 2
    yield
    R._state.clear()


def test_retrieval_returns_cited_ids(small_index):
    r = R.retrieve("fall", {}, k=2)
    assert r and all(c["id"].startswith("KB-") and c["source"] and c["title"] and c["page"] for c in r)
    assert "falls" in r[0]["text"].lower()
    assert R.retrieve("data_gap", {}) == []  # no clinical query for data gaps


def test_rag_disabled_returns_nothing(monkeypatch):
    monkeypatch.setattr(R, "enabled", lambda: False)
    assert R.retrieve("fall", {}) == []


def _val(items, ids=("KB-1", "KB-2"), texts=None):
    guidance = {"section": "guidance", "ids": list(ids), "texts": texts or {"KB-1": DOC}}
    return validate_report({"guidance": items}, {"x": 1}, [], list(ids), [], guidance)


def test_validator_guidance_rules():
    ok = _val([{"text": "Review medicines and home hazards after a fall.", "cites": ["KB-1"]}])
    assert ok["ok"], ok
    assert not _val([{"text": "Review medicines and home hazards.", "cites": []}])["ok"]  # uncited
    bad = _val([{"text": "Review medicines.", "cites": ["KB-99"]}])
    assert not bad["ok"] and any("not retrieved" in e for e in bad["errors"])  # unknown id
    quote = _val([{"text": DOC[:150], "cites": ["KB-1"]}])
    assert not quote["ok"] and any("verbatim" in e for e in quote["errors"])
    diag = _val([{"text": "This is a diagnosis of hypertension.", "cites": ["KB-1"]}])
    assert not diag["ok"]


def test_retrieve_and_xgboost_share_a_process_without_torch():
    """retrieve() must work in a process that also fits an xgboost model, in either order, and never import torch there
    (torch-then-xgboost segfaults on macOS). Embeddings come from echos.knowledge.embed_worker."""
    import subprocess
    import sys
    code = ("import sys\n"
            "{a}\n"
            "from echos.knowledge.retrieve import retrieve\n"
            "r = retrieve('fall', None)\n"
            "{b}\n"
            "assert 'torch' not in sys.modules and 'sentence_transformers' not in sys.modules\n"
            "print('OK', len(r))\n")
    fit = "import numpy as np, xgboost as xgb; X = np.random.rand(100, 3); xgb.XGBClassifier(n_estimators=10).fit(X, X[:, 0] > .5).predict_proba(X)"
    for a, b in ((fit, ""), ("", fit)):
        p = subprocess.run([sys.executable, "-c", code.format(a=a, b=b)], capture_output=True, text=True, timeout=170,
                           cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]))
        assert p.returncode == 0 and "OK" in p.stdout, (p.returncode, p.stderr[-300:])
