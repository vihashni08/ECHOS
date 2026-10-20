"""Rating study kit: item builder (synthetic database, no provenance leaks, deterministic shuffle) and the analysis script with fake ratings."""
import csv
import math

import pytest

from echos.orchestrator import state as S
from scripts.docgen import analyse_ratings as AR
from scripts.docgen import make_rating_sheet as MR
from tests.test_weekly import data, world  # noqa: F401  (world is a fixture)


# ---------------------------------------------------------------- builder
def make_db(tmp_path):
    db = tmp_path / "d.db"
    eng = S.get_engine(db)
    (tmp_path / "reports/daily").mkdir(parents=True)
    spec = [("p1", "2019-05-01", "NORMAL", False, "[]"), ("p2", "2019-05-02", "MONITOR", False, '["vitals alert"]'), ("p3", "2019-05-03", "MONITOR", False, '["vitals alert"]'),
            ("p4", "2019-05-04", "MONITOR", True, '["DATA_GAP: sensor gap"]'), ("p5", "2019-05-05", "URGENT", False, '["possible fall"]'),
            ("p6", "2019-05-06", "URGENT", False, '["possible fall"]')]
    with eng.begin() as c:
        for pid, day, st, gap, rs in spec:
            rel = f"reports/daily/{pid}_{day}.md"
            (tmp_path / rel).write_text(f"# Daily report for {pid} - {day}\n\n**Status: {st}**\n\n- Something changed. [F1]\n\n---\n_Decision-support only, not a medical assessment. Wording: template mode._")
            S.upsert(c, S.case_state, ["patient_id", "day"], patient_id=pid, day=day, status=st, data_gap=gap, days_since_vitals=0, reasons=rs, report=None, updated_ts=1.0)
            S.upsert(c, S.reports, ["patient_id", "day", "kind"], patient_id=pid, day=day, kind="daily", mode="template", path=rel, validation="{}", created_ts=1.0, provenance="{}")
        for i, pid in enumerate(("p5", "p6"), 1):
            aid = f"{pid}|2019-05-0{4 + i}|fall|{i}"
            S.insert_ignore(c, S.alerts, alert_id=aid, patient_id=pid, type="fall", time=1.0, status="SENT", event_id=i, event_time=f"2019-05-0{4 + i}T10:00:00", payload="{}")
            for role in ("caregiver", "clinician"):
                c.execute(S.outbox.insert().values(alert_id=aid, recipient_role=role, address="x@example.test", channel="dryrun", subject=f"[ECHOS URGENT] Possible fall {pid}",
                                                   body="Alert: Possible fall\nFall probability: 0.93\nECHOS is a decision-support system, not a diagnostic tool. This message is a fixed template.",
                                                   status="SENT", attempts=1, event_time=f"2019-05-0{4 + i}T10:00:00"))
    return db


def test_items_cover_the_kinds_and_leak_no_provenance(tmp_path):
    items = MR.build_items(make_db(tmp_path), tmp_path, seed=1)
    kinds = sorted(i["source"] for i in items)
    assert kinds == ["alert/caregiver", "alert/clinician", "daily/data-gap", "daily/fall", "daily/fall", "daily/monitor", "daily/monitor", "daily/normal"]   # no weekly PDFs in this database
    assert [i["item_id"] for i in items] == [f"I{n:02d}" for n in range(1, len(items) + 1)]
    for i in items:
        low = i["text"].lower()
        assert not any(w in low for w in MR.FORBIDDEN) and "[f1]" not in low


def test_shuffle_is_deterministic_and_seed_dependent(tmp_path):
    db = make_db(tmp_path)
    a = [i["source"] for i in MR.build_items(db, tmp_path, seed=3)]
    assert a == [i["source"] for i in MR.build_items(db, tmp_path, seed=3)]
    assert any(a != [i["source"] for i in MR.build_items(db, tmp_path, seed=s)] for s in (4, 5, 6, 7))


def test_kit_files(tmp_path):
    items = MR.build_items(make_db(tmp_path), tmp_path, seed=1)
    out = tmp_path / "rating"
    MR.write_kit(items, out)
    assert (out / "instructions.md").exists() and (out / "items.md").exists() and (out / "private/key.csv").exists() and (out / "filled").is_dir()
    rows = list(csv.reader((out / "rating_sheet.csv").open()))
    assert rows[0] == MR.SHEET_COLS and [r[0] for r in rows[1:]] == [i["item_id"] for i in items]
    assert "template" not in (out / "items.md").read_text().lower()


def test_rating_dir_is_git_ignored():
    from pathlib import Path
    assert "reports/rating/" in (Path(__file__).resolve().parent.parent / ".gitignore").read_text().splitlines()


def test_weekly_text_has_one_line_per_alert_and_no_stamps(world, tmp_path):  # noqa: F811
    from echos.reports.weekly import weekly_pdf as WP
    d = data(world)
    r = WP.build_pdf(d, "Key findings text.", [], tmp_path / "w.pdf")
    t = MR.weekly_text(r["path"])
    assert "Key findings" in t and "Generated" not in t and "Page 1" not in t
    alert_lines = [ln for ln in t.splitlines() if ln.startswith("2019-05-")]
    assert len(alert_lines) == len(d["falls"]) + len(d["vitals_alerts"]) and all(ln.count(" | ") >= 2 for ln in alert_lines)


# ---------------------------------------------------------------- analysis
def write_sheet(folder, rater, rows):
    with (folder / f"{rater}.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(MR.SHEET_COLS)
        for it, c, u, s, a in rows:
            w.writerow([it, rater, c, u, s, a, ""])


def test_means_sds_and_agreement(tmp_path):
    write_sheet(tmp_path, "R1", [("I01", 5, 4, 5, "yes"), ("I02", 2, 2, 3, "no")])
    write_sheet(tmp_path, "R2", [("I01", 3, 4, 5, "yes"), ("I02", 4, 1, 3, "yes")])
    write_sheet(tmp_path, "R3", [("I01", 4, 4, 4, "no"), ("I02", 3, 3, 3, "no")])
    rows, bad = AR.load_ratings(tmp_path)
    s = AR.summarise(rows)
    assert bad == 0 and s["n_raters"] == 3 and s["n_items"] == 2
    m, sd = s["per_item"]["I01"]["clarity"]
    assert m == pytest.approx(4.0) and sd == pytest.approx(1.0)
    assert s["per_item"]["I01"]["clarity_agreement"] == pytest.approx((2 + 1 + 1) / 3)       # |5-3|, |5-4|, |3-4|
    assert s["per_item"]["I01"]["usefulness_agreement"] == 0
    assert s["per_item"]["I01"]["would_act_share"] == pytest.approx(2 / 3)
    assert s["overall"]["clarity"][0] == pytest.approx((5 + 3 + 4 + 2 + 4 + 3) / 6)
    assert s["agreement"]["clarity"] == pytest.approx(((2 + 1 + 1) / 3 + (2 + 1 + 1) / 3) / 2)
    txt = AR.report(s, bad)
    assert "raters: 3" in txt and "agreement" in txt and "I02" in txt


def test_single_rater_has_no_sd_or_agreement(tmp_path):
    write_sheet(tmp_path, "R1", [("I01", 5, 5, 5, "yes")])
    rows, _ = AR.load_ratings(tmp_path)
    s = AR.summarise(rows)
    assert s["n_raters"] == 1 and s["per_item"]["I01"]["clarity"][1] is None and s["agreement"]["clarity"] is None
    assert "needs at least 2 raters" in AR.report(s)


def test_invalid_and_incomplete_rows_are_ignored(tmp_path):
    write_sheet(tmp_path, "R1", [("I01", 6, 4, 5, "yes"), ("I02", 3, 3, 3, "maybe"), ("I03", "", 3, 3, "no"), ("I04", 4, 4, 4, "YES")])
    rows, bad = AR.load_ratings(tmp_path)
    assert bad == 3 and len(rows) == 1 and rows[0]["item_id"] == "I04" and rows[0]["would_act"] is True


def test_empty_folder(tmp_path):
    rows, bad = AR.load_ratings(tmp_path)
    assert rows == [] and AR.report(AR.summarise(rows), bad) == "No valid ratings found."
    assert math.isclose(AR.pairwise_abs_diff([1, 3, 5]), (2 + 4 + 2) / 3)
