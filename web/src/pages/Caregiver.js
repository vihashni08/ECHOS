import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { Info, Siren } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import PatientPicker from "@/components/PatientPicker";
import Provenance from "@/components/Provenance";
import SourceSheet from "@/components/SourceSheet";
import { CardSkeleton, Empty, ErrorNote } from "@/components/States";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { fmtDay } from "@/lib/format";
import { useDaily, usePatients, useStatuses, useSummary } from "@/lib/queries";
import { StatusBadge } from "@/lib/status";
export default function Caregiver() {
    const [sp, setSp] = useSearchParams();
    const patients = usePatients();
    const pid = sp.get("patient") ?? patients.data?.items[0]?.patient_id ?? "";
    const week = useStatuses(pid, 7);
    const strip = week.data ?? [];
    const day = sp.get("day") ?? strip[strip.length - 1]?.day;
    const summary = useSummary(pid, day);
    const daily = useDaily(pid, day);
    const [src, setSrc] = useState(null);
    useEffect(() => { if (!sp.get("patient") && pid)
        setSp((p) => { p.set("patient", pid); return p; }, { replace: true }); }, [pid, sp, setSp]);
    const set = (patch) => setSp((p) => { Object.entries(patch).forEach(([k, v]) => v ? p.set(k, v) : p.delete(k)); return p; });
    const d = daily.data;
    const alerts = d?.alerts?.length ? d.alerts : summary.data?.alerts ?? [];
    return (_jsxs("div", { className: "flex flex-col gap-4", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-3", children: [_jsx("h1", { className: "text-2xl font-semibold", children: "Caregiver" }), _jsx(PatientPicker, { value: pid, onChange: (v) => set({ patient: v, day: "" }) })] }), patients.isLoading ? _jsx(CardSkeleton, { h: "h-64" }) : patients.data?.items.length === 0 ? _jsx(Empty, { title: "No patients yet", children: "Start the data source from Demo tools on the Today page." }) : (_jsxs(_Fragment, { children: [alerts.length > 0 && (_jsxs("div", { role: "alert", className: "flex items-start gap-2.5 rounded-xl border border-urgent-line bg-urgent-bg px-4 py-3 text-urgent-fg", "data-testid": "alert-banner", children: [_jsx(Siren, { className: "mt-0.5 size-5 shrink-0", "aria-hidden": "true" }), _jsx("ul", { className: "space-y-0.5 font-medium", children: alerts.map((a, i) => _jsx("li", { children: a }, i)) })] })), _jsxs(Card, { "data-testid": "daily-card", children: [_jsxs(CardHeader, { className: "flex flex-row flex-wrap items-center justify-between gap-2", children: [_jsxs(CardTitle, { children: ["Today's report \u00B7 ", _jsx("span", { className: "num", children: fmtDay(day) })] }), summary.data && _jsx(StatusBadge, { status: summary.data.status, className: "text-base" })] }), _jsx(CardContent, { className: "flex flex-col gap-4 text-sm", children: summary.isError ? _jsx(ErrorNote, { error: summary.error }) : !summary.data || daily.isLoading ? _jsx(CardSkeleton, { h: "h-40" }) : (_jsxs(_Fragment, { children: [_jsxs("section", { "aria-labelledby": "chg", children: [_jsx("h2", { id: "chg", className: "mb-1 text-base font-semibold", children: "What changed" }), _jsx("ul", { className: "list-disc space-y-1 pl-5", children: (d?.changes?.length ? d.changes : summary.data.reasons.length ? summary.data.reasons : ["Everything looked normal compared with this person's own usual pattern."]).map((c, i) => _jsx("li", { children: c }, i)) })] }), _jsxs("section", { "aria-labelledby": "mon", children: [_jsx("h2", { id: "mon", className: "mb-1 text-base font-semibold", children: "What to monitor today" }), d?.monitor?.length ? _jsx("ul", { className: "list-disc space-y-1 pl-5", children: d.monitor.map((c, i) => _jsx("li", { children: c }, i)) }) : _jsx("p", { className: "text-muted-foreground", children: "No stored report for this day, so no monitoring advice is available." })] }), (d?.data_note ?? summary.data?.data_note) && _jsxs("section", { "aria-labelledby": "dn", children: [_jsx("h2", { id: "dn", className: "mb-1 text-base font-semibold", children: "Data note" }), _jsxs("p", { className: "flex items-start gap-1.5 text-muted-foreground", children: [_jsx(Info, { className: "mt-0.5 size-4 shrink-0", "aria-hidden": "true" }), d?.data_note ?? summary.data?.data_note] })] })] })) })] }), _jsxs("section", { "aria-labelledby": "wk", className: "flex flex-col gap-2", children: [_jsx("h2", { id: "wk", className: "text-base font-semibold", children: "Last 7 days" }), week.isLoading ? _jsx(CardSkeleton, { h: "h-20" }) : strip.length === 0 ? _jsx(Empty, { title: "No days yet" }) : (_jsx("ol", { className: "grid grid-cols-7 gap-1.5", "data-testid": "week-strip", children: strip.map((s) => (_jsx("li", { children: _jsxs("button", { onClick: () => set({ day: s.day }), "aria-current": s.day === day, "aria-label": `${s.day}: ${s.status}`, className: `flex w-full flex-col items-center gap-1 rounded-lg border p-1.5 text-xs transition-colors hover:bg-secondary ${s.day === day ? "border-primary bg-accent" : "bg-card"}`, children: [_jsx("span", { className: "num", children: s.day.slice(5) }), _jsx(StatusBadge, { status: s.status, className: "px-1.5 text-[11px] [&>span]:hidden sm:[&>span]:inline" })] }) }, s.day))) }))] }), d && _jsx(Provenance, { p: d.provenance, mode: d.mode, onSource: setSrc })] })), _jsx(SourceSheet, { id: src, onClose: () => setSrc(null) })] }));
}
