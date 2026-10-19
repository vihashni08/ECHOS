import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { Download, ExternalLink, Eye } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import PatientPicker from "@/components/PatientPicker";
import SendWeekly, { DeliveryStatus } from "@/components/SendWeekly";
import SourceSheet from "@/components/SourceSheet";
import { CardSkeleton, Empty, ErrorNote } from "@/components/States";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { API_URL } from "@/lib/api";
import { fmtEpoch, isoWeek } from "@/lib/format";
import { useBuildWeekly, useOutbox, usePatients, useStatuses, useWeekly, useWeeklyAvailable } from "@/lib/queries";
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function alertRows(d) {
    const rows = [];
    for (const f of d.falls ?? [])
        rows.push({ date: f.day, time: f.time ?? "", type: "Possible fall", detail: `Fall probability ${f.probability_text}; peak ${f.peak_g ?? "n/a"} g; inactivity afterwards: ${f.inactivity ? "yes" : "no"}` });
    for (const a of d.vitals_alerts ?? []) {
        const parts = [["sys", "Systolic BP"], ["dia", "Diastolic BP"], ["hr", "Heart rate"]].flatMap(([k, n]) => a[`${k}_now`] != null && a[`${k}_baseline`] != null ? [`${n} ${a[`${k}_now`]} ${a[`${k}_now`] > a[`${k}_baseline`] ? "above" : "below"} usual ${a[`${k}_baseline`]}`] : []);
        rows.push({ date: a.day, time: "", type: a.kind === "critical" ? "Critical vitals" : "Vitals alert", detail: a.kind === "critical" ? `Critical-range reading: sys ${a.sys ?? "n/a"}, dia ${a.dia ?? "n/a"}, hr ${a.hr ?? "n/a"}` : parts.join("; ") || "Vitals differ from usual" });
    }
    for (const u of d.unusual_days ?? []) {
        const z = Object.entries(u.z ?? {}).filter(([, v]) => v != null).map(([k, v]) => `${k.replace(/_/g, " ")} z ${v > 0 ? "+" : ""}${v}`).join(", ");
        rows.push({ date: u.day, time: "", type: "Unusual activity pattern", detail: `Activity pattern differs from usual${z ? `: ${z}` : ""}` });
    }
    return rows.sort((a, b) => (a.date + a.time).localeCompare(b.date + b.time));
}
export default function Clinician() {
    const [sp, setSp] = useSearchParams();
    const patients = usePatients();
    const pid = sp.get("patient") ?? patients.data?.items[0]?.patient_id ?? "";
    const days = useStatuses(pid, 120);
    const weeks = useMemo(() => [...new Set((days.data ?? []).map((d) => isoWeek(d.day)))], [days.data]);
    const week = sp.get("week") ?? weeks[weeks.length - 1] ?? "";
    const avail = useWeeklyAvailable(pid);
    const stored = avail.data?.includes(week) ?? false;
    const weekly = useWeekly(pid, week, stored);
    const build = useBuildWeekly();
    const built = useRef("");
    const [src, setSrc] = useState(null);
    const outbox = useOutbox(15);
    useEffect(() => { if (!sp.get("patient") && pid)
        setSp((p) => { p.set("patient", pid); return p; }, { replace: true }); }, [pid, sp, setSp]);
    useEffect(() => {
        const key = `${pid}|${week}`;
        if (pid && week && avail.isSuccess && !stored && built.current !== key) {
            built.current = key;
            build.mutate({ pid, week });
        }
    }, [pid, week, avail.isSuccess, stored, build]);
    const w = weekly.data;
    const d = w?.data; // eslint-disable-line @typescript-eslint/no-explicit-any
    const pdf = `${API_URL}/api/patients/${pid}/reports/weekly/pdf?week=${week}`;
    const rows = d ? alertRows(d) : [];
    const sources = (w?.provenance?.sources_retrieved) ?? [];
    const set = (k, v) => setSp((p) => { p.set(k, v); return p; });
    return (_jsxs("div", { className: "flex flex-col gap-4", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-3", children: [_jsx("h1", { className: "text-2xl font-semibold", children: "Clinician" }), _jsxs("div", { className: "flex flex-wrap items-center gap-2", children: [_jsx(PatientPicker, { value: pid, onChange: (v) => setSp({ patient: v }) }), _jsxs(Select, { value: week, onValueChange: (v) => set("week", v), children: [_jsx(SelectTrigger, { className: "w-36", "aria-label": "Choose week", "data-testid": "week-picker", children: _jsx(SelectValue, { placeholder: "Week" }) }), _jsx(SelectContent, { children: [...weeks].reverse().map((x) => _jsx(SelectItem, { value: x, children: x }, x)) })] })] })] }), !pid || !week ? (patients.isLoading || days.isLoading ? _jsx(CardSkeleton, { h: "h-64" }) : _jsx(Empty, { title: "No weeks yet", children: "Weekly reports appear once the simulation has processed a few days." }))
                : weekly.isError ? _jsx(ErrorNote, { error: weekly.error })
                    : !w ? (build.isError ? _jsx(ErrorNote, { error: build.error }) : _jsxs("div", { role: "status", "aria-label": "Building the weekly report", children: [_jsx(CardSkeleton, { h: "h-64" }), _jsx("p", { className: "mt-2 text-sm text-muted-foreground", children: "Building the weekly report\u2026 (nothing is emailed)" })] }))
                        : (_jsxs(_Fragment, { children: [_jsx("div", { className: "grid grid-cols-2 gap-2 md:grid-cols-5", "data-testid": "tiles", children: [
                                        ["Days by status", d ? `Normal ${d.status_counts.NORMAL} · Monitor ${d.status_counts.MONITOR} · Urgent ${d.status_counts.URGENT}` : "n/a", d ? `data gap ${d.status_counts.DATA_GAP}${d.status_counts.NOT_PROCESSED ? ` · not yet processed ${d.status_counts.NOT_PROCESSED}` : ""}` : ""],
                                        ["Vitals alerts", String(d?.vitals_alerts?.length ?? 0), ""], ["Falls", String(d?.falls?.length ?? 0), ""], ["Unusual-pattern days", String(d?.unusual_days?.length ?? 0), ""],
                                        ["Completeness", d ? `vitals ${d.completeness.days_with_vitals}/7 · activity ${d.completeness.days_with_activity}/7` : "n/a", d?.partial_week_note ?? ""],
                                    ].map(([t, v, s]) => (_jsx(Card, { className: "py-3", children: _jsxs(CardContent, { className: "flex flex-col gap-0.5 px-3", children: [_jsx("div", { className: "text-xs text-muted-foreground", children: t }), _jsx("div", { className: "text-sm font-semibold leading-snug", children: v }), s && _jsx("div", { className: "text-xs text-muted-foreground", children: s })] }) }, t))) }), _jsxs(Card, { children: [_jsx(CardHeader, { children: _jsxs(CardTitle, { children: ["Key findings \u00B7 ", _jsxs("span", { className: "num", children: [w.week_start, " to ", w.week_end] })] }) }), _jsxs(CardContent, { className: "flex flex-col gap-3 text-sm", children: [_jsx("p", { "data-testid": "narrative", className: "leading-relaxed", children: w.narrative ?? "No narrative stored." }), _jsxs("p", { className: "text-xs text-muted-foreground", children: ["Written by ", w.mode === "llm" ? "an AI model, checked by an automatic validator" : "a fixed template", "."] })] })] }), _jsxs(Card, { children: [_jsx(CardHeader, { children: _jsx(CardTitle, { children: "Alerts" }) }), _jsxs(CardContent, { children: [rows.length === 0 ? _jsx("p", { className: "text-sm text-muted-foreground", children: "No alerts this week." }) : (_jsx("div", { className: "overflow-x-auto", children: _jsxs("table", { className: "w-full text-left text-sm", "data-testid": "alerts-table", children: [_jsx("thead", { className: "text-xs text-muted-foreground", children: _jsxs("tr", { children: [_jsx("th", { className: "pb-1 pr-3 font-medium", children: "Date" }), _jsx("th", { className: "pb-1 pr-3 font-medium", children: "Time" }), _jsx("th", { className: "pb-1 pr-3 font-medium", children: "Type" }), _jsx("th", { className: "pb-1 font-medium", children: "Detail" })] }) }), _jsx("tbody", { children: rows.map((r, i) => _jsxs("tr", { className: "border-t align-top", children: [_jsx("td", { className: "num py-1.5 pr-3", children: r.date }), _jsx("td", { className: "num py-1.5 pr-3", children: r.time }), _jsx("td", { className: "py-1.5 pr-3 font-medium", children: r.type }), _jsx("td", { className: "py-1.5", children: r.detail })] }, i)) })] }) })), d && _jsxs("p", { className: "mt-3 text-xs text-muted-foreground", "data-testid": "dq-line", children: ["Data quality: room-sensor activity on ", d.completeness.days_with_activity, " of 7 days, vitals on ", d.completeness.days_with_vitals, " of 7 days, ", d.completeness.sensor_gap_days, " sensor-gap day(s), ", d.completeness.invalid_values_removed, " invalid value(s) removed, longest stretch without vitals ", d.completeness.longest_vitals_gap_days, " day(s).", d.partial_week_note ? ` ${d.partial_week_note}` : ""] })] })] }), _jsxs(Card, { children: [_jsx(CardHeader, { children: _jsx(CardTitle, { children: "References (for clinician review)" }) }), _jsx(CardContent, { children: sources.length === 0 ? _jsx("p", { className: "text-sm text-muted-foreground", children: "No guideline sources were retrieved for this week." }) : (_jsx("ul", { className: "flex flex-col gap-1.5 text-sm", "data-testid": "references", children: sources.map((s) => (_jsx("li", { children: _jsxs("button", { onClick: () => setSrc(s.id), className: "flex w-full items-start gap-2 rounded-md border px-3 py-2 text-left hover:bg-secondary", "aria-label": `Open source ${s.id}: ${s.title}`, children: [_jsxs("span", { className: "num shrink-0 font-semibold text-primary", children: ["[", s.id, "]"] }), _jsxs("span", { children: [s.title, " ", _jsxs("span", { className: "text-muted-foreground", children: ["\u00B7 ", s.source] })] })] }) }, s.id))) })) })] }), _jsxs("div", { className: "flex flex-wrap items-start gap-3", children: [_jsx(Button, { asChild: true, variant: "outline", children: _jsxs("a", { href: pdf, download: `${pid}_${week}.pdf`, "data-testid": "download-pdf", children: [_jsx(Download, { className: "size-4", "aria-hidden": "true" }), "Download PDF"] }) }), _jsx(Button, { asChild: true, variant: "outline", children: _jsxs("a", { href: pdf, target: "_blank", rel: "noreferrer noopener", "data-testid": "preview-pdf", children: [_jsx(Eye, { className: "size-4", "aria-hidden": "true" }), "Preview PDF", _jsx(ExternalLink, { className: "size-3.5", "aria-hidden": "true" })] }) }), _jsx(SendWeekly, { pid: pid, week: week })] })] })), _jsxs(Card, { children: [_jsx(CardHeader, { children: _jsxs(CardTitle, { children: ["Outbox ", _jsx("span", { className: "text-sm font-normal text-muted-foreground", children: "(newest 8)" })] }) }), _jsx(CardContent, { children: !outbox.data?.items.length ? _jsx("p", { className: "text-sm text-muted-foreground", children: "Nothing in the outbox yet." }) : (_jsx("ul", { className: "flex flex-col divide-y text-sm", "data-testid": "outbox-list", children: outbox.data.items.slice(0, 8).map((o) => (_jsxs("li", { className: "flex flex-col gap-0.5 py-2 sm:flex-row sm:items-center sm:gap-4", children: [_jsx("span", { className: "num text-muted-foreground sm:w-28", children: fmtEpoch(o.queued_time) }), _jsx("span", { className: "min-w-0 flex-1 break-words font-medium", children: o.kind === "weekly" ? `Weekly report${o.attachment_name ? ` (${o.attachment_name})` : ""}` : "Alert" }), _jsxs("span", { children: [_jsx("span", { className: "capitalize", children: o.recipient_role }), " \u00B7 ", _jsx("span", { className: "num", children: o.address })] }), _jsxs("span", { className: "flex items-center gap-3", children: [_jsx(DeliveryStatus, { o: o }), _jsx("span", { children: o.delivery === "dry_run" ? "Dry run" : "Email" })] })] }, o.outbox_id))) })) })] }), _jsx(SourceSheet, { id: src, onClose: () => setSrc(null) })] }));
}
