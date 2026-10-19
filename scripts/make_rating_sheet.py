"""Build the rating-study kit from the (regenerated) demo database, which is opened READ-ONLY: 10 items with provenance removed and shuffled.
  6 daily caregiver reports (Normal, Monitor x2, data-gap, fall x2) + 2 weekly clinician summaries + 2 immediate fall alerts.
Writes reports/rating/ (git-ignored): items.md (what raters read), instructions.md, rating_sheet.csv (one row per item, to be filled per rater),
private/key.csv (item -> source; raters must not see it). Template-mode reports only: no LLM, no PubMed, no e-mail.
Usage: PYTHONPATH=. python scripts/make_rating_sheet.py [--db data/echos.db] [--seed 42] [--out reports/rating]"""
import argparse
import csv
import random
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHEET_COLS = ["item_id", "rater_id", "clarity_1_5", "usefulness_1_5", "safe_wording_1_5", "would_act_yes_no", "comment"]
KIND_TITLE = {"daily": "Daily report for the caregiver", "weekly": "Weekly summary for the clinician", "alert": "Immediate alert e-mail"}
SECTIONS = ["Key findings", "Vitals vs personal baseline", "Daily routine vs personal baseline", "Alerts", "Findings and supporting sources",
            "Considerations for clinician review", "Data quality"]
FORBIDDEN = ("template", "llm", "gemini", "validator", "provenance", "wording:", "cache", "fallback")


def scrub(text: str) -> str:
    """Remove anything that reveals how a text was made (wording mode, evidence tags, generation stamps, page footers)."""
    t = re.sub(r"\s*Wording:[^_\n]*", "", text)
    t = re.sub(r"\s?\[F\d+\]", "", t)
    t = re.sub(r"\s*This message is a fixed template\.", "", t)
    t = re.sub(r"\s*Generated \d{4}-\d{2}-\d{2} \d{2}:\d{2}\.?", "", t)
    t = re.sub(r"^Page \d+ of \d+$|^Patient \w+$", "", t, flags=re.M)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


STATUS = ("NORMAL", "MONITOR", "URGENT", "no result")
TYPES = ("Possible fall", "Vitals alert", "Critical vitals", "Unusual activity pattern")


def _alert_row(cells: list[str]) -> str:
    """['2019-04-08', '15:16', 'Possible fall', 'detail line', 'detail line 2', 'URGENT'] -> 'date time | type | detail | day status'."""
    date, rest = cells[0], cells[1:]
    time = rest.pop(0) if rest and re.match(r"\d{2}:\d{2}$", rest[0]) else ""
    typ = rest.pop(0) if rest and rest[0] in TYPES else ""
    status = rest.pop() if rest and rest[-1] in STATUS else ""
    return f"{date} {time}".strip() + f" | {typ} | " + " ".join(rest) + (f" | day status {status}" if status else "")


def weekly_text(pdf_path: str) -> str:
    """Clinician-readable plain text of a weekly PDF: header, key findings, alerts (one line each), sources, considerations, data quality."""
    from pypdf import PdfReader
    lines = [ln.rstrip() for pg in PdfReader(pdf_path).pages for ln in pg.extract_text().splitlines()]
    head = scrub(lines[1]).rstrip(" |") if len(lines) > 1 else ""
    sec, cur = {}, None
    for ln in lines:
        if ln.strip() in SECTIONS:
            cur = ln.strip()
            sec.setdefault(cur, [])
        elif cur:
            sec[cur].append(ln)
    out = ["Weekly clinician report", head, ""]
    for name in SECTIONS:
        body = sec.get(name)
        if not body or name in ("Vitals vs personal baseline", "Daily routine vs personal baseline"):
            continue
        if name == "Alerts":     # the table is extracted one cell per line: regroup by the date that starts each row
            rows, cell = [], None
            for ln in body[5:]:
                if re.match(r"\d{4}-\d{2}-\d{2}$", ln.strip()):
                    cell = [ln.strip()]
                    rows.append(cell)
                elif cell is not None:
                    cell.append(ln.strip())
            rows = [_alert_row(r) for r in rows]
            body = rows or ["No alerts this week."]
        else:
            body = [b for b in body if not b.startswith("Chart legend") and not b.startswith("Decision support, not a diagnosis") and not b.startswith("are simulated")]
        out += [name, *body, ""]
    out.append("Decision support, not a diagnosis. Fall signals are simulated.")
    return scrub("\n".join(out))


def _rows(con, sql, *a):
    con.row_factory = sqlite3.Row
    return [dict(r) for r in con.execute(sql, a).fetchall()]


def pick(rng, rows, n, key="patient_id"):
    """n rows, preferring different patients; deterministic for a seeded rng."""
    rows = sorted(rows, key=lambda r: (r["patient_id"], r["day"]))
    rng.shuffle(rows)
    out, seen = [], set()
    for r in rows:
        if r[key] not in seen:
            out.append(r)
            seen.add(r[key])
        if len(out) == n:
            return out
    return (out + [r for r in rows if r not in out])[:n]


def build_items(db: Path, root: Path, seed: int = 42) -> list[dict]:
    rng = random.Random(seed)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    cases = _rows(con, "select patient_id, day, status, data_gap, reasons from case_state")
    daily = {(r["patient_id"], r["day"]): r["path"] for r in _rows(con, "select patient_id, day, path from reports where kind='daily'")}
    falls = {(r["patient_id"], r["event_time"][:10]) for r in _rows(con, "select patient_id, event_time from alerts where type='fall'")}
    have = [c for c in cases if (c["patient_id"], c["day"]) in daily and (root / daily[(c["patient_id"], c["day"])]).exists()]
    normal = [c for c in have if c["status"] == "NORMAL" and not c["data_gap"]]
    monitor = [c for c in have if c["status"] == "MONITOR" and not c["data_gap"]]
    gap = [c for c in have if c["data_gap"] and (c["patient_id"], c["day"]) not in falls]
    fall_days = [c for c in have if (c["patient_id"], c["day"]) in falls]
    by_reason = lambda k: [c for c in monitor if k in c["reasons"].lower()]  # noqa: E731
    first = pick(rng, by_reason("vitals"), 1)
    second = pick(rng, [c for c in monitor if c not in first and "vitals" not in c["reasons"].lower()] or [c for c in monitor if c not in first], 1)
    chosen = [("daily/normal", c) for c in pick(rng, normal, 1)] + [("daily/monitor", c) for c in first + second] + \
             [("daily/data-gap", c) for c in pick(rng, gap, 1)] + [("daily/fall", c) for c in pick(rng, fall_days, 2)]
    items = []
    for src, c in chosen:
        text = (root / daily[(c["patient_id"], c["day"])]).read_text()
        items.append({"kind": "daily", "source": src, "patient_id": c["patient_id"], "day": c["day"], "text": scrub(text)})
    wk = []
    for r in _rows(con, "select patient_id, day, path from reports where kind='weekly_pdf'"):
        p = Path(r["path"]) if Path(r["path"]).exists() else root / "data/reports/weekly" / Path(r["path"]).name
        if p.exists():
            wk.append({**r, "text": weekly_text(str(p))})
    rich = [w for w in wk if "Considerations for clinician review" in w["text"] and "Possible fall" in w["text"]] or wk
    vit = [w for w in rich if "Vitals alert" in w["text"]]
    for w in (pick(rng, vit, 1) + pick(rng, [w for w in rich if w not in vit] or rich, 1))[:2]:
        items.append({"kind": "weekly", "source": "weekly/clinician", "patient_id": w["patient_id"], "day": w["day"], "text": w["text"]})
    al = _rows(con, "select o.recipient_role, o.subject, o.body, a.patient_id, substr(a.event_time,1,10) as day from outbox o join alerts a on a.alert_id=o.alert_id "
                    "where a.type='fall'")
    for role, r in zip(("caregiver", "clinician"), pick(rng, [a for a in al if a["recipient_role"] == "caregiver"], 1) + pick(rng, [a for a in al if a["recipient_role"] == "clinician" and a["patient_id"] not in {x["patient_id"] for x in items if x["kind"] == "alert"}], 1)):
        items.append({"kind": "alert", "source": f"alert/{role}", "patient_id": r["patient_id"], "day": r["day"], "text": scrub(r["subject"] + "\n\n" + r["body"])})
    rng.shuffle(items)
    for i, it in enumerate(items, 1):
        it["item_id"] = f"I{i:02d}"
        bad = [w for w in FORBIDDEN if w in it["text"].lower()]
        assert not bad, f"item {it['item_id']} still reveals provenance: {bad}"
    return items


INSTRUCTIONS = """# Rating study: instructions

You are rating short texts produced by ECHOS, a decision-support system for people living at home (not a diagnostic tool). Everything you read is based on
a research dataset; fall signals are simulated; no real person is affected. There are 10 items: daily reports for a caregiver, weekly summaries for a
clinician and immediate fall alerts. You are not told how any text was produced.

## What to do
1. Read `items.md`, one item at a time, in the order shown. Rate each item on your own: please do not discuss the items with other raters.
2. Copy `rating_sheet.csv` to `filled/<your-rater-id>.csv` (any short id, e.g. R1) and fill one row per item. Put your id in `rater_id` on every row.
3. Rate as the intended reader: the caregiver (daily reports, alerts) or the clinician (weekly summaries).

## Scales (whole numbers 1-5)
| Column | 1 | 5 |
|---|---|---|
| `clarity_1_5`: can you tell what happened and what is being said? | very unclear | very clear |
| `usefulness_1_5`: does it give what the reader needs to decide what to do next? | not useful | very useful |
| `safe_wording_1_5`: is the wording safe, i.e. no diagnosis, no medication advice, no false reassurance, no alarm beyond the facts? | unsafe | safe |

`would_act_yes_no`: write `yes` if you would act on this text as its reader (call, visit, review, repeat a reading), otherwise `no`.
`comment`: optional, free text (what confused you, what was missing, any wording you would change). No names or personal details.

## Returning your sheet
Keep one CSV per rater in `reports/rating/filled/`. Rows with a missing or out-of-range score are reported and ignored by `scripts/analyse_ratings.py`.
"""


def write_kit(items: list[dict], out: Path) -> None:
    (out / "private").mkdir(parents=True, exist_ok=True)
    (out / "filled").mkdir(exist_ok=True)
    md = ["# Items to rate", ""]
    for it in items:
        md += [f"## Item {it['item_id']}: {KIND_TITLE[it['kind']]}", "", it["text"], "", "---", ""]
    (out / "items.md").write_text("\n".join(md))
    (out / "instructions.md").write_text(INSTRUCTIONS)
    with (out / "rating_sheet.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(SHEET_COLS)
        for it in items:
            w.writerow([it["item_id"], "", "", "", "", "", ""])
    with (out / "private" / "key.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["item_id", "kind", "source", "patient_id", "day"])
        for it in items:
            w.writerow([it["item_id"], it["kind"], it["source"], it["patient_id"], it["day"]])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data/echos.db"))
    ap.add_argument("--root", default=str(ROOT), help="directory the stored report paths are relative to")
    ap.add_argument("--out", default=str(ROOT / "reports/rating"))
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    items = build_items(Path(a.db), Path(a.root), a.seed)
    write_kit(items, Path(a.out))
    from collections import Counter
    print(len(items), "items:", dict(Counter(i["source"] for i in items)), "->", a.out)


if __name__ == "__main__":
    main()
