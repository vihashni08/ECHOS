"""Weekly clinician report as a PDF (ReportLab + matplotlib Agg), A4, at most 3 pages, one fixed layout:
header -> summary tiles -> Key findings -> vitals vs personal baseline -> (one chart legend note) -> alerts table -> findings and supporting sources ->
considerations for clinician review -> data quality; footer on every page.
Status is never shown by colour alone: tiles and tables carry text labels, flagged points use a different marker shape plus a legend entry, and every day tick carries a status letter (N Normal, M Monitor, U Urgent, - no result). No vitals model score appears anywhere."""
from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.pdfgen import canvas as rl_canvas  # noqa: E402
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle  # noqa: E402

from echos.reports.weekly.weekly_data import VARS  # noqa: E402

LETTER = {"NORMAL": "N", "MONITOR": "M", "URGENT": "U", None: "-"}
INK, MUTED, LINE = colors.HexColor("#1f2937"), colors.HexColor("#6b7280"), colors.HexColor("#d1d5db")
W_MM = 180
FOOTER = ("Decision support, not a diagnosis. ECHOS processed rule-based and machine-learning agents over home-sensor and vitals records; fall signals are "
          "simulated. Baselines are each person's own past data. Generated {gen}.")


_CHECK: list | None = None      # tests set this to [] to collect (axes box, caption box) pairs of every chart


def _png(fig, dpi=130) -> bytes:
    if _CHECK is not None:
        fig.canvas.draw()
        for ax in fig.axes:
            for tx in ax.texts:
                if tx.get_gid() == "caption":
                    _CHECK.append((ax.get_window_extent(), tx.get_window_extent()))
    b = io.BytesIO()
    fig.savefig(b, format="png", dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return b.getvalue()


def _ticks(d: dict):
    days = [x["day"] for x in d["days"]]
    pos = [pd.Timestamp(x) for x in days]
    lab = [f"{pd.Timestamp(x).strftime('%a %d')}\n[{LETTER[s['status']]}]" for x, s in zip(days, d["days"])]
    return pos, lab


def _style_ax(ax, d, ylabel):
    pos, lab = _ticks(d)
    ax.set_xticks([p + pd.Timedelta(hours=12) for p in pos])
    ax.set_xticklabels(lab, fontsize=6)
    ax.set_xlim(pos[0], pos[-1] + pd.Timedelta(days=1))
    ax.set_ylabel(ylabel, fontsize=7)
    ax.tick_params(axis="y", labelsize=7)
    ax.grid(alpha=.25, lw=.5)
    ax.spines[["top", "right"]].set_visible(False)


def vitals_chart(d: dict, h_mm: float) -> bytes:
    fig, axs = plt.subplots(3, 2, figsize=(7.2, h_mm / 25.4 * 1.0), constrained_layout=True)
    ser = d["series"]["vitals"]
    handles: dict = {}
    for ax, k in zip(axs.flat, ["sys", "dia", "hr", "temp", "weight"]):
        name, unit = VARS[k]
        s = ser[k]
        for day, b in s["band"].items():
            if b:
                t = pd.Timestamp(day)
                ax.fill_between([t, t + pd.Timedelta(days=1)], b[0], b[1], color="#9ca3af", alpha=.3, lw=0, label="personal band (usual +/- 2 SD)")
                ax.plot([t, t + pd.Timedelta(days=1)], [b[2], b[2]], color="#6b7280", lw=.8, ls="--", label="personal baseline")
        ok = [p for p in s["points"] if not p["flagged"]]
        fl = [p for p in s["points"] if p["flagged"]]
        if ok:
            ax.plot([pd.Timestamp(p["t"]) for p in ok], [p["value"] for p in ok], "o", ms=3, color="#1d4ed8", label="reading")
        if fl:
            ax.plot([pd.Timestamp(p["t"]) for p in fl], [p["value"] for p in fl], "X", ms=7, color="#b91c1c", mec="black", mew=.5, label="flagged (outside band)")
        if not s["points"]:       # nothing to plot: a compact text panel instead of an empty chart
            ax.axis("off")
            ax.text(0, .85, name, transform=ax.transAxes, fontsize=8, va="top")
            ax.text(0, .6, "no readings this week", transform=ax.transAxes, fontsize=7, va="top", color="#6b7280")
            continue
        ax.set_title(name, fontsize=8, loc="left", pad=11)
        if s["points"] and not d["vitals_summary"][k]["baseline_established"]:     # caption under the chart title, never over the data
            ax.text(0, 1.02, "personal baseline not yet established (fewer than 5 earlier readings)", transform=ax.transAxes, fontsize=5.5, va="bottom", color="#6b7280", gid="caption")
        _style_ax(ax, d, f"{name} ({unit})")
        h, l = ax.get_legend_handles_labels()
        handles.update(dict(zip(l, h)))
    axs[2, 1].axis("off")
    axs[2, 1].legend(handles.values(), handles.keys(), fontsize=6.5, frameon=False, loc="center left", title="Legend", title_fontsize=7)
    return _png(fig)


class _Numbered(rl_canvas.Canvas):
    """Two-pass canvas: footer with 'Page x of y' on every page."""
    footer, gen, pid = "", "", ""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._saved = []

    def showPage(self):
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        n = len(self._saved)
        for st in self._saved:
            self.__dict__.update(st)
            self.setStrokeColor(LINE)
            self.line(15 * mm, 17 * mm, 195 * mm, 17 * mm)
            self.setFont("Helvetica", 6.5)
            self.setFillColor(MUTED)
            words, line, y = self.footer.split(), "", 13.5 * mm
            for w in words:
                if self.stringWidth(line + " " + w, "Helvetica", 6.5) > 150 * mm:
                    self.drawString(15 * mm, y, line.strip())
                    line, y = "", y - 3 * mm
                line += " " + w
            self.drawString(15 * mm, y, line.strip())
            self.drawRightString(195 * mm, 13.5 * mm, f"Page {self._pageNumber} of {n}")
            self.drawString(15 * mm, 6 * mm, f"Patient {self.pid}")
            super().showPage()
        super().save()


def _styles():
    ss = getSampleStyleSheet()
    base = ParagraphStyle("b", parent=ss["Normal"], fontName="Helvetica", fontSize=8.5, leading=11, textColor=INK)
    return {"b": base, "small": ParagraphStyle("s", parent=base, fontSize=7.5, leading=9.5, textColor=MUTED),
            "h1": ParagraphStyle("h1", parent=base, fontName="Helvetica-Bold", fontSize=17, leading=20),
            "h2": ParagraphStyle("h2", parent=base, fontName="Helvetica-Bold", fontSize=11, leading=14, spaceBefore=7, spaceAfter=3),
            "tile": ParagraphStyle("t", parent=base, fontSize=7.5, leading=10, alignment=1), "cell": ParagraphStyle("c", parent=base, fontSize=7.5, leading=9)}


def _alert_rows(d: dict) -> list:
    st = {x["day"]: x["status"] for x in d["days"]}
    lab = lambda day: (st.get(day) or "no result")  # noqa: E731
    rows = []
    for f in d["falls"]:
        pk = f"{f['peak_g']:g} g" if f.get("peak_g") is not None else "not recorded"
        rows.append((f["day"], f["time"] or "", "Possible fall", f"Fall probability {f['probability_text']}; peak {pk}; inactivity afterwards: {'yes' if f['inactivity'] else 'no'} (simulated signal)", lab(f["day"])))
    for a in d["vitals_alerts"]:
        if a.get("detail"):
            det = ("Critical-range reading: " if a["kind"] == "critical" else "") + a["detail"]
        else:      # rows built before the values were stored
            parts = []
            for k, n in (("sys", "Systolic BP"), ("dia", "Diastolic BP"), ("hr", "Heart rate")):
                now, base = a.get(f"{k}_now"), a.get(f"{k}_baseline")
                if now is not None and base is not None:
                    parts.append(f"{n} {now:g} {'above' if now > base else 'below'} usual {base:g}")
            det = "; ".join(parts) or "Vitals differ from usual; values not stored for this day"
        if a.get("why"):
            det += ". Why it fired: " + a["why"]
        rows.append((a["day"], "", "Vitals alert" if a["kind"] != "critical" else "Critical vitals", det, lab(a["day"])))
    return sorted(rows)


def build_pdf(d: dict, narrative: str, sources: list, out_path, generated_at: datetime | None = None, max_pages: int = 3,
              findings: list | None = None, considerations: list | None = None, narrative_items: dict | None = None) -> dict:
    """Write the PDF; returns {path, pages, bytes, headings}. Layout is shrunk step by step if it would exceed max_pages."""
    gen = (generated_at or datetime.now()).strftime("%Y-%m-%d %H:%M")
    for scale in (1.0, .85, .7, .55):
        out = _build(d, narrative, sources, out_path, gen, scale, findings, considerations, narrative_items)
        if out["pages"] <= max_pages:
            return out
    return out


def _build(d, narrative, sources, out_path, gen, scale, findings=None, considerations=None, narrative_items=None) -> dict:
    st, c, comp = _styles(), d["status_counts"], d["completeness"]
    P = lambda t, s="b": Paragraph(t, st[s])  # noqa: E731
    story, headings = [], []

    def h(t):
        headings.append(t)
        return Paragraph(escape(t), st["h2"])
    headings.append("Weekly clinician report")
    story += [P("Weekly clinician report", "h1"),
              P(f"Patient (de-identified id) <b>{escape(d['patient_id'])}</b> &nbsp;|&nbsp; Age band <b>{escape(d['age_band'])}</b> &nbsp;|&nbsp; Sex <b>{escape(d['sex'])}</b> "
                f"&nbsp;|&nbsp; Week <b>{d['week']}</b> ({d['week_start']} to {d['week_end']}) &nbsp;|&nbsp; Generated {gen}", "small"), Spacer(1, 3 * mm)]
    headings.append("Summary")
    n_np = c.get("NOT_PROCESSED", 0)
    tiles = [[f"<b>Days by status</b><br/>({comp['days_with_data']} days with data)<br/>Normal {c['NORMAL']} | Monitor {c['MONITOR']}<br/>Urgent {c['URGENT']}"
              + (f" | Not processed {n_np}" if n_np else "") + f"<br/><font size=6.5 color='#6b7280'>Data-gap days (counted within the above): {c['DATA_GAP']}</font>",
              f"<b>Vitals alerts</b><br/>{len(d['vitals_alerts'])}", f"<b>Falls</b><br/>{len(d['falls'])}",
              f"<b>Completeness</b><br/>Vitals {comp['days_with_vitals']}/7 days<br/>Activity {comp['days_with_activity']}/7 days"]]
    t = Table([[Paragraph(x, st["tile"]) for x in tiles[0]]], colWidths=[W_MM / 4 * mm] * 4)
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), .6, LINE), ("INNERGRID", (0, 0), (-1, -1), .6, LINE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f3f4f6")), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    story += [Paragraph("Summary", st["h2"]), t]
    if d["partial_week_note"]:
        story += [Spacer(1, 1.5 * mm), P(escape(d["partial_week_note"]), "small")]
    imp = [i["text"] for i in (narrative_items or {}).get("impression", [])]
    if imp:
        story += [h("Impression"), P(escape(" ".join(imp)))]
    kf = [i["text"] for i in (narrative_items or {}).get("key_findings", [])]
    story += [h("Key findings")] + ([P("&bull; " + escape(x)) for x in kf] if kf else [P(escape(narrative))])
    story += [KeepTogether([h("Vitals vs personal baseline"), Image(io.BytesIO(vitals_chart(d, 150 * scale)), width=W_MM * mm, height=W_MM * mm * (150 * scale) / 183.0 * 1.0)])]
    story += [P(f"Chart legend: under each day [N] Normal, [M] Monitor, [U] Urgent, [-] no result (week of {d['week_start']}). Grey band = this person's usual range; "
                "dashed line = personal baseline; X = reading outside the band. Bands appear only where a baseline is established.", "small")]
    headings.append("Alerts")
    rows = _alert_rows(d)
    shown = rows[:14 if scale == 1.0 else 10]
    data = [["Date", "Time", "Type", "Detail", "Day status"]] + [[Paragraph(escape(str(x)), st["cell"]) for x in r] for r in shown] if shown else [["Date", "Time", "Type", "Detail", "Day status"], [Paragraph("No alerts this week.", st["cell"])] + [""] * 4]
    if len(rows) > len(shown):
        data.append([Paragraph(f"+ {len(rows) - len(shown)} more alert(s) not shown", st["cell"])] + [""] * 4)
    at = Table(data, colWidths=[22 * mm, 13 * mm, 33 * mm, 90 * mm, 22 * mm], repeatRows=1)
    at.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 7.5), ("LINEBELOW", (0, 0), (-1, 0), .8, INK),
                            ("LINEBELOW", (0, 1), (-1, -1), .3, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP")] + ([("SPAN", (0, 1), (-1, 1))] if not shown else [])))
    story += [KeepTogether([Paragraph("Alerts", st["h2"]), at])]
    q = [f"Days with room-sensor activity: {comp['days_with_activity']} of 7. Days with vitals readings: {comp['days_with_vitals']} of 7.",
         f"Sensor-gap days: {comp['sensor_gap_days']}. Invalid values removed: {comp['invalid_values_removed']}. Longest stretch without vitals: {comp['longest_vitals_gap_days']} day(s).",
         f"Days with data: {comp['days_with_data']} of 7." + (f" {d['partial_week_note']}" if d["partial_week_note"] else "")]
    nb = [VARS[k][0] for k in VARS if d["vitals_summary"][k]["n_readings"] and not d["vitals_summary"][k]["baseline_established"]]
    if nb:
        q.append("Personal baseline not yet established (fewer than 5 earlier readings) for: " + ", ".join(nb) + ". Only this week's own values are shown for them.")
    by_id = {s_["id"]: s_ for s_ in sources}
    num = {s_["id"]: i for i, s_ in enumerate(sources, 1)}
    if findings is None:
        from echos.reports.evidence import present_findings
        findings = [{**f, "sources": []} for f in present_findings(d)]
    from echos.reports.evidence import LABELS
    lab = {f["key"]: f["label"].split(":")[0] for f in findings}
    crit_keys = {k for a_ in d["vitals_alerts"] if a_["kind"] == "critical" for k in (a_.get("types") or [])}
    prio = lambda key: "Within 2 days" if key == "falls" or key in crit_keys else "This week"      # noqa: E731
    refs_of = lambda ids: " ".join(f"[{num[i]}]" for i in ids if i in num)                       # noqa: E731
    # ---- findings and their numbered sources (full citation: title, publisher / journal, year, page, link)
    from echos.reports.daily.document import cite_label
    headings.append("Findings and supporting sources")
    fs = [Paragraph("Findings and supporting sources", st["h2"])]
    for f in findings:
        fs.append(P(f"<b>{escape(f['label'])}</b>"))
        srcs = [by_id[i] for i in f["sources"] if i in by_id]
        fs += [P(f"&nbsp;&nbsp;{num[s_['id']]}. {escape(str(s_['title']).rstrip('. '))}. {escape(cite_label(s_))}. <font color='#6b7280'>[{escape(s_['id'])}]</font>"
                 + (f" {escape(s_['url'])}" if s_.get("url") else ""), "small") for s_ in srcs]
        if not srcs:
            fs.append(P("&nbsp;&nbsp;No supporting source retrieved for this finding.", "small"))
    if not findings:
        fs.append(P("No findings this week that call for supporting sources.", "small"))
    story += [KeepTogether(fs)]
    # ---- recommended follow-up: one row per finding (the cited, concrete bullet when there is one, else a plain pointer to the alerts table)
    rows_fu, have = [], set()
    merged: dict = {}
    for b_ in considerations or []:                   # one row per finding: the first bullet's text, the sources of all its bullets
        m = merged.setdefault(b_["finding"], {"text": b_["text"], "cites": []})
        m["cites"] += [c_ for c_ in b_["cites"] if c_ not in m["cites"]]
    for key_, m in merged.items():
        have.add(key_)
        rows_fu.append((prio(key_), m["text"].rstrip(". ") + ".", f"{lab.get(key_, key_)} {refs_of(m['cites'])}".strip()))
    for f in findings:
        if f["key"] not in have:
            rows_fu.append((prio(f["key"]), f"Review the {lab[f['key']].lower()} entries in the alerts table with the caregiver; no supporting source was retrieved for this finding.", lab[f["key"]]))
    rows_fu.sort(key=lambda r: (r[0] != "Within 2 days", r[0]))
    headings.append("Recommended follow-up")
    fu = [Paragraph("Recommended follow-up (for clinician review)", st["h2"])]
    if rows_fu:
        ft = Table([["When", "Follow-up", "Basis"]] + [[Paragraph(escape(x), st["cell"]) for x in r] for r in rows_fu], colWidths=[24 * mm, 118 * mm, 38 * mm], repeatRows=1)
        ft.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 7.5), ("LINEBELOW", (0, 0), (-1, 0), .8, INK),
                                ("LINEBELOW", (0, 1), (-1, -1), .3, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        fu.append(ft)
    else:
        fu.append(P("No findings this week call for a specific follow-up. Routine review as scheduled.", "small"))
    story += [KeepTogether(fu)]
    story += [h("Data quality")] + [P(escape(x)) for x in q]

    _Numbered.footer, _Numbered.pid = FOOTER.format(gen=gen), d["patient_id"]
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=13 * mm, bottomMargin=21 * mm,
                            title=f"ECHOS weekly report {d['patient_id']} {d['week']}", author="ECHOS")
    doc.build(story, canvasmaker=_Numbered)
    from pypdf import PdfReader
    pages = len(PdfReader(str(path)).pages)
    return {"path": str(path), "pages": pages, "bytes": path.stat().st_size, "headings": headings}
