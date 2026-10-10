"""Populate data/echos.db by running the full pipeline (replay -> agents -> orchestrator -> reports) on a few TIHM patients.
Usage: PYTHONPATH=. python3 scripts/run_demo.py [n_patients] [max_days]   (systems demo, not an accuracy result)"""
import sys
import warnings

warnings.filterwarnings("ignore")
from echos.config import ROOT  # noqa: E402
from scripts.kill_test import run_pipeline  # noqa: E402

if __name__ == "__main__":
    n, d = (int(a) for a in sys.argv[1:3]) if len(sys.argv) > 2 else (4, 28)
    (ROOT / "data").mkdir(exist_ok=True)
    for f in ("echos.db", "echos.db-wal", "echos.db-shm", "cp.sqlite"):
        (ROOT / "data" / f).unlink(missing_ok=True)
    r = run_pipeline(ROOT / "data", None, n_patients=n, max_days=d)
    print("cases", r["n_cases"], "alerts", len(r["alerts"]))
