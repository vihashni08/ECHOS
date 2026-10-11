"""Elderly (subject SE06) fall sensitivity at each audit threshold, with and without post-fall inactivity confirmation.
Same v2 windows, same subject-level folds and random forest as scripts/fall_audit.py (evaluation only: no model is saved,
the operating point is NOT changed). Adds `fall_elderly_by_threshold` to reports/metrics/model_selection.json and a script-written
block to reports/RESULTS.md. Usage: PYTHONPATH=. python scripts/elderly_by_threshold.py"""
import json
import warnings

warnings.filterwarnings("ignore")
from echos.agents import fall as F  # noqa: E402
from echos.config import ROOT, load_config  # noqa: E402

OUT = ROOT / "reports/metrics/model_selection.json"
THR = [0.5, 0.6, 0.7, 0.8, 0.9]
START, END = "<!-- elderly_se06:start -->", "<!-- elderly_se06:end -->"


def main():
    seed = load_config()["seed"]
    v2 = F.build_windows_v2().reset_index(drop=True)
    oof = F.score_oof(v2, seed)
    el = v2[(v2.group == "elderly") & (v2.kind == "fall")]
    subjects, n = sorted(el.subject.unique().tolist()), int(len(el))
    rows = []
    for t in THR:
        r = {"threshold": t}
        for k, conf in (("without_inactivity", False), ("with_inactivity", True)):
            s = F.metrics_at(v2, oof, t, conf)["groups"]["elderly"]["sensitivity"]
            r[k] = {"sensitivity": float(s), "falls_detected": int(round(s * n))}
        rows.append(r)
    res = {"subjects": subjects, "n_falls": n, "rows": rows, "operating_point": load_config()["agent3"]["operating"]}
    d = json.loads(OUT.read_text())
    d["fall_elderly_by_threshold"] = res
    OUT.write_text(json.dumps(d, indent=2, default=float))
    lo = min(r["with_inactivity"]["sensitivity"] for r in rows)
    hi = max(r["without_inactivity"]["sensitivity"] for r in rows)
    op = res["operating_point"]
    opr = next(r for r in rows if r["threshold"] == op["threshold"])["with_inactivity" if op["require_inactivity"] else "without_inactivity"]
    md = [START, "### Elderly fall sensitivity by threshold (written by scripts/elderly_by_threshold.py)", "",
          f"Subject {', '.join(subjects)} is the only elderly subject with falls ({n} falls). Random forest, v2 windows, subject-level folds.", "",
          "| Threshold | Without inactivity confirmation | With inactivity confirmation |", "|---|---|---|"]
    md += [f"| {r['threshold']} | {r['without_inactivity']['sensitivity']:.3f} ({r['without_inactivity']['falls_detected']}/{n}) | "
           f"{r['with_inactivity']['sensitivity']:.3f} ({r['with_inactivity']['falls_detected']}/{n}) |" for r in rows]
    md += ["", f"**Limitation:** elderly sensitivity comes from one subject ({', '.join(subjects)}, {n} scripted falls) and ranges from {lo:.2f} to {hi:.2f} across thresholds "
           f"and confirmation settings; at the chosen operating point (threshold {op['threshold']}, inactivity confirmation "
           f"{'required' if op['require_inactivity'] else 'not required'}) it is {opr['sensitivity']:.2f} ({opr['falls_detected']}/{n}). "
           "It cannot be generalised to elderly people, and requiring inactivity confirmation lowers elderly sensitivity more than adult sensitivity.", END]
    p = ROOT / "reports/RESULTS.md"
    s = p.read_text()
    block = "\n".join(md)
    if START in s:
        s = s[:s.index(START)] + block + s[s.index(END) + len(END):]
    else:
        k = s.index("<!-- model_selection:end -->") + len("<!-- model_selection:end -->")
        s = s[:k] + "\n\n" + block + s[k:]
    p.write_text(s)
    print("| thr | no confirm | with confirm |")
    for r in rows:
        print(f"| {r['threshold']} | {r['without_inactivity']['sensitivity']:.3f} ({r['without_inactivity']['falls_detected']}/{n}) | {r['with_inactivity']['sensitivity']:.3f} ({r['with_inactivity']['falls_detected']}/{n}) |")


if __name__ == "__main__":
    main()
