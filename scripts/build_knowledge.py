"""Build the guideline index for the Evidence agent from data/knowledge/raw/ (PDF / txt / html; git-ignored publisher material).

    PYTHONPATH=. python scripts/build_knowledge.py
"""
from echos.evidence import corpus

if __name__ == "__main__":
    r = corpus.build_index()
    print(f"indexed {r['chunks']} chunks from {', '.join(r['documents'])} -> {corpus.INDEX}")
