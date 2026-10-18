"""Eyeball RAG relevance: for 10 typical findings print the top-1 retrieved source title, page and score.
Usage: PYTHONPATH=. python3 research/rag_check.py   (run `python3 -m echos.knowledge.ingest` first)"""
from echos.knowledge.retrieve import build_query, retrieve

CASES = [("fall", {}), ("fall_inactivity", {"post_fall_inactive": True}), ("fall", {"kind": "alert"}),
         ("blood_pressure", {"kind": "alert"}), ("blood_pressure", {"kind": "critical"}), ("vitals_news2", {}),
         ("vitals_news2", {"kind": "critical"}), ("agitation", {}), ("routine_change", {}), ("agitation", {"kind": "alert"})]

if __name__ == "__main__":
    print(f"{'finding':<16}{'detail':<22}{'top-1 source (page)':<44}{'score':>6}  title")
    for ft, d in CASES:
        r = retrieve(ft, d, k=3)
        top = r[0] if r else None
        print(f"{ft:<16}{(','.join(f'{k}={v}' for k, v in d.items()) or '-'):<22}"
              f"{(top['id'] + ' ' + top['source'] + ' p' + str(top['page'])) if top else 'NONE':<44}{top['score'] if top else '':>6}  {top['title'][:48] if top else ''}")
