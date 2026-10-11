"""Query-embedding helper process. torch (sentence-transformers) and xgboost's OpenMP runtime can crash a process that
loads torch first (segfault, macOS), so only this process loads torch. Protocol: one JSON line per request on stdin
({"texts": [...]}), one JSON line back ({"vectors": [[...]]} or {"error": "..."}). Run: python -m echos.knowledge.embed_worker
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading

_lock = threading.Lock()
_proc: dict = {}


def _serve() -> None:
    from echos.knowledge.ingest import get_model
    model = get_model()
    for line in sys.stdin:
        try:
            texts = json.loads(line)["texts"]
            out = {"vectors": model.encode(texts, normalize_embeddings=True).tolist()}
        except Exception as ex:  # noqa: BLE001
            out = {"error": type(ex).__name__}
        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()


def _start():
    from echos.config import ROOT
    env = {**os.environ, "PYTHONPATH": str(ROOT), "TOKENIZERS_PARALLELISM": "false"}
    p = subprocess.Popen([sys.executable, "-W", "ignore", "-m", "echos.knowledge.embed_worker"], cwd=ROOT, env=env,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    _proc["p"] = p
    return p


def encode(texts: list, timeout_s: float = 120.0) -> list:
    """Embed texts in the helper process (started lazily, reused). Raises RuntimeError on failure."""
    with _lock:
        for attempt in (0, 1):
            p = _proc.get("p")
            if p is None or p.poll() is not None:
                p = _start()
            try:
                p.stdin.write(json.dumps({"texts": texts}) + "\n")
                p.stdin.flush()
                res: dict = {}
                t = threading.Thread(target=lambda: res.update(json.loads(p.stdout.readline() or "{}")), daemon=True)
                t.start()
                t.join(timeout_s)
                if t.is_alive():
                    p.kill()
                    raise RuntimeError("embed worker timeout")
                if "vectors" in res:
                    return res["vectors"]
                raise RuntimeError(res.get("error", "embed worker died"))
            except (BrokenPipeError, OSError, RuntimeError, ValueError):
                p.kill()
                _proc.pop("p", None)
                if attempt:
                    raise
    raise RuntimeError("unreachable")


def close() -> None:
    p = _proc.pop("p", None)
    if p and p.poll() is None:
        p.kill()


import atexit  # noqa: E402

atexit.register(close)

if __name__ == "__main__":
    _serve()
