"""10-report real-LLM smoke test (uses llm.model / key from config + .env). Prints an outcome table and, if errors
remain, the 3 most common error messages. Usage: PYTHONPATH=. python3 scripts/llm_smoke.py [n]"""
import sys
import tempfile
import warnings
from collections import Counter
from pathlib import Path

warnings.filterwarnings("ignore")
from echos.orchestrator import state as S
from echos.reports import agent as A
from echos.reports.llm import LLMAPIError, LLMTimeout, make_llm
from tests.test_reports import facts

n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
llm = make_llm()
if llm is None:
    sys.exit("no LLM configured (llm.model / ECHOS_LLM_API_KEY)")
tmp = Path(tempfile.mkdtemp())
ag = A.ReportAgent(S.get_engine(tmp / "t.db"), llm, root=tmp)
outcomes, msgs = Counter(), Counter()
for i in range(n):
    out = ag.daily(facts(day=f"2019-05-{i + 1:02d}", sys_now=150.0 + i))
    v, notes = out["validation"], out["validation"].get("notes", [])
    r = v.get("fallback_reason")
    if out["mode"] == "llm" and not notes:
        k = "success first time"
    elif out["mode"] == "llm":
        k = "success after retry"
    elif r.startswith("validation"):
        k = "validator failure"
    elif r.startswith("timeout"):
        k = "timeout"
    elif " 429 " in f" {r} ":
        k = "429"
    else:
        k = "other client error"
    outcomes[k] += 1
    if k not in ("success first time", "success after retry"):
        msgs[(r or "")[:160] if k != "validator failure" else "; ".join(notes)[:160]] += 1
print(f"| outcome | n/{n} |\n|---|---|")
for k in ["success first time", "success after retry", "validator failure", "timeout", "429", "other client error"]:
    print(f"| {k} | {outcomes.get(k, 0)} |")
if msgs:
    print("top errors:")
    for m, c in msgs.most_common(3):
        print(f"  {c}x {m}")
