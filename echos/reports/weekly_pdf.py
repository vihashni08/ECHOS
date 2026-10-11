"""Weekly clinician report as a PDF (ReportLab + matplotlib Agg), A4, at most 3 pages, one fixed layout:
header -> summary tiles -> Key findings -> vitals vs personal baseline -> daily routine -> alerts table -> data quality -> references; footer on every page.
Status is never shown by colour alone: tiles and tables carry text labels, flagged points use a different marker shape plus a legend entry, unusual days are
hatched, and every day tick carries a status letter (N Normal, M Monitor, U Urgent, - no result). No agitation or vitals model score appears anywhere."""
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

from echos.reports.weekly_data import ROUTINE, VARS  # noqa: E402

LETTER = {"NORMAL": "N", "MONITOR": "M", "URGENT": "U", None: "-"}
INK, MUTED, LINE = colors.HexColor("#1f2937"), colors.HexColor("#6b7280"), colors.HexColor("#d1d5db")
W_MM = 180
FOOTER = ("Decision support, not a diagnosis. ECHOS processed rule-based and machine-learning agents over home-sensor and vitals records; fall signals are "
          "simulated. Baselines are each person's own past data. Generated {gen}.")


def _png(fig, dpi=130) -> bytes:
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
    ax.set_xlabel(f"Day ({pos[0].strftime('%b %Y')}); [N]ormal [M]onitor [U]rgent [-] no result", fontsize=6)
    ax.tick_params(axis="y", labelsize=7)
    ax.grid(alpha=.25, lw=.5)
    ax.spines[["top", "right"]].set_visible(False)


def vitals_chart(d: dict, h_mm: float) -> bytes:
    fig, axs = plt.subplots(3, 2, figsize=(7.2, h_mm / 25.4 * 1.0), constrained_layout=True)
    ser = d["series"]["vitals"]
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
        if not s["points"]:
            ax.text(.5, .5, "no readings this week", transform=ax.transAxes, ha="center", fontsize=7, color="#6b7280")
        elif not d["vitals_summary"][k]["baseline_established"]:
            ax.text(.5, .04, "personal baseline not yet established\n(fewer than 5 earlier readings)", transform=ax.transAxes, ha="center", fontsize=6, color="#6b7280")
        ax.set_title(name, fontsize=8, loc="left")
        _style_ax(ax, d, f"{name} ({unit})")
        h, l = ax.get_legend_handles_labels()
        u = dict(zip(l, h))
        if u:
            ax.legend(u.values(), u.keys(), fontsize=5, frameon=False, loc="best")
    axs[2, 1].axis("off")
    axs[2, 1].text(0, .6, "Grey band: this person's usual range\n(past 28 days, mean +/- 2 SD).\nX marks a reading outside the band.\n"
                          "[N]/[M]/[U] under each day: the day's status.", fontsize=7, va="center")
    return _png(fig)


def routine_chart(d: dict, h_mm: float) -> bytes:
    fig, axs = plt.subplots(3, 1, figsize=(7.2, h_mm / 25.4), constrained_layout=True)
    unusual = {u["day"] for u in d["unusual_days"]}
    units = {"night_events": "events per night", "night_bathroom": "visits per night", "total_events": "events per day"}
    for ax, k in zip(axs, ["night_events", "night_bathroom", "total_events"]):
        r = d["series"]["routine"][k]
        for day, v in r["values"].items():
            t = pd.Timestamp(day) + pd.Timedelta(hours=12)
            b = r["band"][day]
            if b:
                ax.fill_between([pd.Timestamp(day), pd.Timestamp(day) + pd.Timedelta(days=1)], max(b[0], 0), b[1], color="#9ca3af", alpha=.3, lw=0, label="personal band (usual +/- 2 SD)")
                ax.plot([pd.Timestamp(day), pd.Timestamp(day) + pd.Timedelta(days=1)], [b[2], b[2]], color="#6b7280", lw=.8, ls="--", label="personal baseline")
            if v is not None:
                ax.bar(t, v, width=.6, color="#1d4ed8", hatch="//" if day in unusual else None, edgecolor="white" if day not in unusual else "black", lw=.5,
                       label="unusual activity pattern day" if day in unusual else "daily value")
            else:
                ax.text(t, 0, "no data", fontsize=5, ha="center", va="bottom", color="#6b7280")
        if not d["routine_baseline_established"][k]:
            ax.text(.5, .9, "personal baseline not yet established (fewer than 7 earlier days)", transform=ax.transAxes, ha="center", fontsize=6, color="#6b7280")
        ax.set_title(ROUTINE[k], fontsize=8, loc="left")
        _style_ax(ax, d, units[k])
        h, l = ax.get_legend_handles_labels()
        u = dict(zip(l, h))
        ax.legend(u.values(), u.keys(), fontsize=5, frameon=False, loc="upper right", ncol=2)
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
        pk = f"{f['peak_g']:g} g" if f["peak_g"] is not None else "n/a"
        rows.append((f["day"], f["time"] or "", "Possible fall", f"Fall probability {f['probability_text']}; peak {pk}; inactivity afterwards: {'yes' if f['inactivity'] else 'no'}", lab(f["day"])))
    for a in d["vitals_alerts"]:
        if a["kind"] == "critical":
            det = "Critical-range reading: " + ", ".join(f"{k} {a[k]:g}" for k in ("sys", "dia", "hr") if a.get(k) is not None)
        else:
            parts = []
            for k, n in (("sys", "Systolic BP"), ("dia", "Diastolic BP"), ("hr", "Heart rate")):
                now, base = a.get(f"{k}_now"), a.get(f"{k}_baseline")
                if now is not None and base is not None:
                    parts.append(f"{n} {now:g} {'above' if now > base else 'below'} usual {base:g}")
            det = "; ".join(parts) or "Vitals differ from usual"
        rows.append((a["day"], "", "Vitals alert" if a["kind"] != "critical" else "Critical vitals", det, lab(a["day"])))
    for u in d["unusual_days"]:
        z = ", ".join(f"{ROUTINE[k].lower()} z {v:+g}" for k, v in u["z"].items() if v is not None) or "no personal z-scores available"
        rows.append((u["day"], "", "Unusual activity pattern", f"Activity pattern differs from usual: {z}", lab(u["day"])))
    return sorted(rows)


def build_pdf(d: dict, narrative: str, sources: list, out_path, generated_at: datetime | None = None, max_pages: int = 3) -> dict:
    """Write the PDF; returns {path, pages, bytes, headings}. Layout is shrunk step by step if it would exceed max_pages."""
    gen = (generated_at or datetime.now()).strftime("%Y-%m-%d %H:%M")
    for scale in (1.0, .85, .7, .55):
        out = _build(d, narrative, sources, out_path, gen, scale)
        if out["pages"] <= max_pages:
            return out
    return out


def _build(d, narrative, sources, out_path, gen, scale) -> dict:
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
    tiles = [[f"<b>Days by status</b><br/>Normal {c['NORMAL']} | Monitor {c['MONITOR']}<br/>Urgent {c['URGENT']} | Data gap {c['DATA_GAP']}",
              f"<b>Vitals alerts</b><br/>{len(d['vitals_alerts'])}", f"<b>Falls</b><br/>{len(d['falls'])}",
              f"<b>Unusual-pattern days</b><br/>{len(d['unusual_days'])}",
              f"<b>Completeness</b><br/>Vitals {comp['days_with_vitals']}/7 days<br/>Activity {comp['days_with_activity']}/7 days"]]
    t = Table([[Paragraph(x, st["tile"]) for x in tiles[0]]], colWidths=[W_MM / 5 * mm] * 5)
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), .6, LINE), ("INNERGRID", (0, 0), (-1, -1), .6, LINE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f3f4f6")), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    story += [Paragraph("Summary", st["h2"]), t]
    if d["partial_week_note"]:
        story += [Spacer(1, 1.5 * mm), P(escape(d["partial_week_note"]), "small")]
    story += [h("Key findings"), P(escape(narrative))]
    story += [KeepTogether([h("Vitals vs personal baseline"), Image(io.BytesIO(vitals_chart(d, 150 * scale)), width=W_MM * mm, height=W_MM * mm * (150 * scale) / 183.0 * 1.0)])]
    img = Image(io.BytesIO(routine_chart(d, 125 * scale)), width=W_MM * mm, height=W_MM * mm * (125 * scale) / 183.0)
    story += [KeepTogether([h("Daily routine vs personal baseline"), img])]
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
    story += [h("Data quality")] + [P(escape(x)) for x in q]
    headings.append("References (for clinician review)")
    refs = [P(f"[{escape(s['id'])}] {escape(s['title'])} - {escape(s['source'])}" + (f" - {escape(s['url'])}" if s.get("url") else ""), "small") for s in sources]
    story += [Paragraph("References (for clinician review)", st["h2"])] + (refs or [P("No guideline sources were retrieved (no Monitor or Urgent day, or sources unavailable).", "small")])

    _Numbered.footer, _Numbered.pid = FOOTER.format(gen=gen), d["patient_id"]
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=13 * mm, bottomMargin=21 * mm,
                            title=f"ECHOS weekly report {d['patient_id']} {d['week']}", author="ECHOS")
    doc.build(story, canvasmaker=_Numbered)
    from pypdf import PdfReader
    pages = len(PdfReader(str(path)).pages)
    return {"path": str(path), "pages": pages, "bytes": path.stat().st_size, "headings": headings}
