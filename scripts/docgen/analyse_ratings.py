"""Summarise the filled rating sheets in reports/rating/filled/*.csv: number of raters, mean and SD per item and overall, and agreement
(mean pairwise absolute difference between raters, per item and scale; 0 = identical scores). Rows with a missing or out-of-range score are ignored and counted.
Usage: PYTHONPATH=. python scripts/docgen/analyse_ratings.py [dir]"""
import csv
import itertools
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCALES = {"clarity_1_5": "clarity", "usefulness_1_5": "usefulness", "safe_wording_1_5": "safe wording"}


def load_ratings(folder: Path) -> tuple[list[dict], int]:
    """-> (valid rows [{item_id, rater_id, clarity, usefulness, safe_wording, would_act (bool)}], number of ignored rows)."""
    rows, bad = [], 0
    for f in sorted(Path(folder).glob("*.csv")):
        with f.open(newline="") as fh:
            for r in csv.DictReader(fh):
                try:
                    rec = {"item_id": r["item_id"].strip(), "rater_id": (r.get("rater_id") or f.stem).strip() or f.stem}
                    for col, name in SCALES.items():
                        v = int(str(r[col]).strip())
                        if not 1 <= v <= 5:
                            raise ValueError
                        rec[name] = v
                    ans = str(r["would_act_yes_no"]).strip().lower()
                    if ans not in ("yes", "no"):
                        raise ValueError
                    rec["would_act"] = ans == "yes"
                    rows.append(rec)
                except (KeyError, ValueError):
                    bad += 1
    return rows, bad


def mean_sd(x: list[float]) -> tuple[float, float | None]:
    return st.mean(x), (st.stdev(x) if len(x) > 1 else None)


def pairwise_abs_diff(x: list[float]) -> float | None:
    """Mean absolute difference over all pairs of raters for one item; None with fewer than 2 raters."""
    pairs = list(itertools.combinations(x, 2))
    return sum(abs(a - b) for a, b in pairs) / len(pairs) if pairs else None


def summarise(rows: list[dict]) -> dict:
    names = list(SCALES.values())
    raters = sorted({r["rater_id"] for r in rows})
    items = sorted({r["item_id"] for r in rows})
    per_item, agree = {}, {n: [] for n in names}
    yn_agree = []
    for it in items:
        rs = [r for r in rows if r["item_id"] == it]
        d = {"n": len(rs), "would_act_share": sum(r["would_act"] for r in rs) / len(rs)}
        for n in names:
            d[n] = mean_sd([r[n] for r in rs])
            ad = pairwise_abs_diff([r[n] for r in rs])
            d[n + "_agreement"] = ad
            if ad is not None:
                agree[n].append(ad)
        ya = [a == b for a, b in itertools.combinations([r["would_act"] for r in rs], 2)]
        if ya:
            yn_agree.append(sum(ya) / len(ya))
        per_item[it] = d
    overall = {n: mean_sd([r[n] for r in rows]) for n in names} if rows else {}
    return {"n_raters": len(raters), "raters": raters, "n_items": len(items), "per_item": per_item, "overall": overall,
            "overall_would_act_share": (sum(r["would_act"] for r in rows) / len(rows)) if rows else None,
            "agreement": {n: (st.mean(v) if v else None) for n, v in agree.items()},
            "would_act_agreement": st.mean(yn_agree) if yn_agree else None}


def fmt(ms) -> str:
    m, s = ms
    return f"{m:.2f} (SD {s:.2f})" if s is not None else f"{m:.2f} (SD n/a)"


def report(s: dict, ignored: int = 0) -> str:
    if not s["n_raters"]:
        return "No valid ratings found."
    names = list(SCALES.values())
    out = [f"raters: {s['n_raters']} ({', '.join(s['raters'])}); items: {s['n_items']}; ignored incomplete / invalid rows: {ignored}", "",
           "item | n | " + " | ".join(names) + " | would act", "---|---|" + "|".join(["---"] * (len(names) + 1))]
    for it, d in s["per_item"].items():
        out.append(f"{it} | {d['n']} | " + " | ".join(fmt(d[n]) for n in names) + f" | {d['would_act_share']:.0%}")
    out += ["", "overall | | " + " | ".join(fmt(s["overall"][n]) for n in names) + f" | {s['overall_would_act_share']:.0%}", ""]
    if s["n_raters"] < 2:
        out.append("agreement: needs at least 2 raters")
    else:
        out.append("agreement (mean pairwise absolute difference, 0 = identical; scale 1-5): " + "; ".join(
            f"{n} {s['agreement'][n]:.2f}" for n in names if s["agreement"][n] is not None)
            + (f"; would act: {s['would_act_agreement']:.0%} of rater pairs agree" if s["would_act_agreement"] is not None else ""))
    return "\n".join(out)


def main():
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "reports/rating/filled"
    rows, bad = load_ratings(folder)
    print(report(summarise(rows), bad))


if __name__ == "__main__":
    main()
