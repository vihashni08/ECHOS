import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Bot, CheckCircle2, ListChecks, Route, ShieldAlert } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { CardSkeleton } from "@/components/States";
import { useCase } from "@/lib/queries";
import { fmtDay } from "@/lib/format";
import { StatusBadge } from "@/lib/status";
import { cn } from "@/lib/utils";
const LEVEL = {
    STRONG: { text: "Strong evidence", cls: "bg-urgent-bg text-urgent-fg border-urgent-line", hint: "An absolute rule fired or two independent sources agree. Not a probability." },
    MODERATE: { text: "Moderate evidence", cls: "bg-monitor-bg text-monitor-fg border-monitor-line", hint: "One flag that repeats or persists. Not a probability." },
    WATCH: { text: "Watch only", cls: "bg-gap-bg text-gap-fg border-gap-line", hint: "No rule fired; a detector is close to its own alert threshold." },
    LIMITED: { text: "Limited evidence", cls: "bg-gap-bg text-gap-fg border-gap-line", hint: "A single flag, or the data has caveats. Not a probability." },
};
const TOOL = { nearest_threshold: "Found the detector closest to its alert threshold", read_detectors: "Read what the detectors found", check_history: "Compared with the last days", check_agreement: "Checked whether sources agree", check_data_caveats: "Looked for data problems", check_trend: "Checked the trend of the readings", check_personal_history: "Looked at this person's earlier deviations", check_corroboration: "Checked the routine against the vitals" };
const DET = { vitals: "Vitals detector", behaviour: "Behaviour detector", fall: "Fall detector" };
function stepText(t) {
    const r = t.result;
    if (t.tool === "read_detectors") {
        const f = r.flagged ?? [];
        return f.length ? `Flagged by: ${f.map((x) => DET[x] ?? x).join(", ")}` : "No detector flagged anything; the case comes from the data checks";
    }
    if (t.tool === "nearest_threshold")
        return `${DET[r.detector] ?? r.detector} at ${Math.round(Number(r.ratio) * 100)}% of its alert threshold`;
    if (t.tool === "check_history")
        return `${r.non_normal_days} changed day(s) in the last ${r.days_seen}; ${r.streak_before_today} in a row before today`;
    if (t.tool === "check_agreement")
        return `${r.independent_sources} independent source(s) point the same way`;
    if (t.tool === "check_data_caveats") {
        const c = t.result ?? [];
        return c.length ? c.join(" ") : "No data problems found";
    }
    if (t.tool === "check_trend") {
        const c = r.confirmed ?? [];
        const o = r.outside_usual ?? [];
        return r.available ? (c.length ? `On a trend away from the usual: ${c.join(", ")}` : o.length ? `Outside the usual (${o.join(", ")}) but not on a trend` : "Readings within the usual") : "Not enough earlier readings";
    }
    if (t.tool === "check_personal_history")
        return r.available ? `${r.prior_deviation_days} earlier day(s) outside their usual; ${r.prior_deviation_days_with_event} carried a recorded clinical event` : "Not enough earlier history";
    if (t.tool === "check_corroboration") {
        const u = r.routine_unusual ?? [];
        return r.available ? (u.length ? `Activity also outside the usual: ${u.map((x) => x.replace("_", " ")).join(", ")}` : "Activity within the usual") : "No activity data to compare";
    }
    return "";
}
/** "How this decision was made": the Case Investigator's file. Only changed or urgent days have one. */
export default function CaseFilePanel({ pid, day, status }) {
    const c = useCase(pid, day);
    if (c.isLoading)
        return _jsx(CardSkeleton, { h: "h-40" });
    if (!c.data)
        return _jsx(Card, { children: _jsx(CardContent, { className: "pt-4 text-sm text-muted-foreground", "data-testid": "case-none", children: status && status !== "NORMAL" ? "No case file was built for this day: the Case Investigator did not run on it (for example a replay made before it existed). The reasons above still come from the detectors." : "No case file: the latest day is normal, so the Case Investigator has nothing to investigate." }) });
    const f = { ...c.data, specifics: c.data.specifics ?? [], next_checks: c.data.next_checks ?? [], caveats: c.data.caveats ?? [], trace: c.data.trace ?? [], reasons: c.data.reasons ?? [] };
    const lv = LEVEL[f.evidence_level] ?? LEVEL.LIMITED;
    return (_jsxs(Card, { "data-testid": "case-file", children: [_jsxs(CardHeader, { className: "flex flex-row flex-wrap items-center justify-between gap-2", children: [_jsxs(CardTitle, { className: "flex items-center gap-2", children: [_jsx(Bot, { className: "size-5", "aria-hidden": "true" }), "Case file \u00B7 ", _jsx("span", { className: "num", children: fmtDay(f.day) })] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsx(StatusBadge, { status: f.status }), _jsx("span", { title: lv.hint, className: cn("rounded-full border px-2.5 py-0.5 text-xs font-semibold", lv.cls), "data-testid": "evidence-level", children: lv.text })] })] }), _jsxs(CardContent, { className: "flex flex-col gap-4 text-sm", children: [_jsx("p", { className: "text-base font-medium", "data-testid": "case-headline", children: f.headline }), f.specifics.length > 0 && (_jsxs("div", { "data-testid": "case-specifics", children: [_jsx("h3", { className: "mb-1 font-semibold", children: "What was recorded" }), _jsx("ul", { className: "list-disc space-y-1 pl-5", children: f.specifics.map((x, i) => _jsx("li", { children: x }, i)) })] })), f.next_checks.length > 0 && (_jsxs("div", { children: [_jsxs("h3", { className: "mb-1 flex items-center gap-1.5 font-semibold", children: [_jsx(ListChecks, { className: "size-4", "aria-hidden": "true" }), "Suggested next checks"] }), _jsx("ul", { className: "list-disc space-y-1 pl-5", children: f.next_checks.map((x, i) => _jsx("li", { children: x }, i)) })] })), f.caveats.length > 0 && (_jsxs("div", { className: "rounded-md border border-monitor-line bg-monitor-bg p-3 text-monitor-fg", children: [_jsxs("h3", { className: "mb-1 flex items-center gap-1.5 font-semibold", children: [_jsx(ShieldAlert, { className: "size-4", "aria-hidden": "true" }), "Weakens the evidence"] }), _jsx("ul", { className: "list-disc space-y-1 pl-5", children: f.caveats.map((x, i) => _jsx("li", { children: x }, i)) })] })), _jsxs("details", { className: "rounded-md border p-3", "data-testid": "case-trace", children: [_jsxs("summary", { className: "flex cursor-pointer items-center gap-1.5 font-semibold", children: [_jsx(Route, { className: "size-4", "aria-hidden": "true" }), "How the Case Investigator reached this (", f.trace.length, " steps)"] }), _jsx("ol", { className: "mt-2 space-y-2", children: f.trace.map((t) => (_jsxs("li", { className: "flex gap-2", children: [_jsx(CheckCircle2, { className: "mt-0.5 size-4 shrink-0 text-normal-fg", "aria-hidden": "true" }), _jsxs("div", { children: [_jsx("div", { className: "font-medium", children: TOOL[t.tool] ?? String(t.tool) }), _jsx("div", { className: "text-muted-foreground", children: String(t.why) }), _jsx("div", { children: stepText(t) })] })] }, t.step))) }), _jsxs("p", { className: "mt-2 text-xs text-muted-foreground", children: ["Rules that fired: ", f.reasons.length ? f.reasons.join("; ") : "none (data check only)"] })] }), _jsx("p", { className: "text-xs text-muted-foreground", children: f.disclaimer })] })] }));
}
