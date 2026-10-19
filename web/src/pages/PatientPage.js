import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useEffect, useMemo, useState } from "react";
import { Navigate, useNavigate, useParams } from "react-router-dom";
import { Info } from "lucide-react";
import BandBars from "@/charts/BandBars";
import Heatmap from "@/charts/Heatmap";
import VitalChart from "@/charts/VitalChart";
import TeamPlanPanel from "@/components/TeamPlanPanel";
import CaseFilePanel from "@/components/CaseFilePanel";
import { CardSkeleton, Empty, ErrorNote } from "@/components/States";
import Timeline from "@/components/Timeline";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { usePatients, useRoutine, useStatuses, useSummary, useVitals } from "@/lib/queries";
import { StatusBadge } from "@/lib/status";
import { fmtDay } from "@/lib/format";
const addDays = (d, n) => new Date(new Date(d).getTime() + n * 86400000).toISOString().slice(0, 10);
export function PatientRedirect() {
    const p = usePatients();
    if (p.isLoading)
        return _jsx(CardSkeleton, { h: "h-24" });
    const first = p.data?.items[0];
    return first ? _jsx(Navigate, { to: `/patients/${first.patient_id}`, replace: true }) : _jsx(Empty, { title: "No patients yet", children: "Open Demo tools on the Today page and start the data source." });
}
function WhyFlagged({ pid }) {
    const s = useSummary(pid);
    if (s.isLoading)
        return _jsx(CardSkeleton, { h: "h-28" });
    if (s.isError)
        return _jsx(ErrorNote, { error: s.error });
    const d = s.data;
    return (_jsxs(Card, { "data-testid": "why-flagged", children: [_jsxs(CardHeader, { className: "flex flex-row items-center justify-between gap-2", children: [_jsxs(CardTitle, { children: ["Why flagged \u00B7 ", _jsx("span", { className: "num", children: fmtDay(d.day) })] }), _jsx(StatusBadge, { status: d.status })] }), _jsxs(CardContent, { className: "flex flex-col gap-2 text-sm", children: [d.reasons.length === 0 ? _jsx("p", { children: "Nothing unusual compared with this person's own pattern." }) : _jsx("ul", { className: "list-disc space-y-1 pl-5", children: d.reasons.map((r, i) => _jsx("li", { children: r }, i)) }), d.data_note && _jsxs("p", { className: "flex items-start gap-1.5 text-muted-foreground", children: [_jsx(Info, { className: "mt-0.5 size-4 shrink-0", "aria-hidden": "true" }), d.data_note] })] })] }));
}
export default function PatientPage() {
    const { id = "" } = useParams();
    const nav = useNavigate();
    const patients = usePatients();
    const me = patients.data?.items.find((p) => p.patient_id === id);
    const st = useStatuses(id, 120);
    const days = st.data?.map((d) => d.day) ?? [];
    const min = days[0], max = days[days.length - 1];
    const [from, setFrom] = useState(""), [to, setTo] = useState("");
    useEffect(() => { if (max) {
        setTo(max);
        setFrom(min && addDays(max, -13) < min ? min : addDays(max, -13));
    } }, [id, min, max]);
    const ready = useMemo(() => !!from && !!to && from <= to, [from, to]);
    const vit = useVitals(id, ready ? from : undefined, ready ? to : undefined);
    const rou = useRoutine(id, ready ? from : undefined, ready ? to : undefined);
    if (patients.isSuccess && !me)
        return _jsx(Empty, { title: `Unknown patient ${id}` });
    const v = vit.data;
    return (_jsxs("div", { className: "flex flex-col gap-4", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-3", children: [_jsxs("div", { children: [_jsxs("h1", { className: "text-2xl font-semibold", children: ["Patient ", _jsx("span", { className: "num", children: id })] }), _jsx("p", { className: "text-sm text-muted-foreground", children: me ? `Age ${me.age_band} · ${me.sex}` : "…" }), _jsxs("div", { className: "mt-2 flex items-center gap-2 text-sm", children: [_jsx("span", { className: "text-muted-foreground", children: "Last 14 days" }), _jsx(Timeline, { pid: id, days: 14 })] })] }), _jsxs("div", { className: "flex flex-wrap items-center gap-2", children: [me && _jsx(StatusBadge, { status: me.latest_status }), _jsxs(Select, { value: id, onValueChange: (v) => nav(`/patients/${v}`), children: [_jsx(SelectTrigger, { className: "w-32", "aria-label": "Choose patient", children: _jsx(SelectValue, {}) }), _jsx(SelectContent, { children: patients.data?.items.map((p) => _jsx(SelectItem, { value: p.patient_id, children: p.patient_id }, p.patient_id)) })] })] })] }), _jsx(WhyFlagged, { pid: id }), _jsx(CaseFilePanel, { pid: id, status: me?.latest_status }), _jsx(TeamPlanPanel, { pid: id }), _jsxs("div", { className: "flex flex-wrap items-end gap-3 text-sm", children: [_jsxs("label", { className: "flex flex-col gap-1", children: ["From", _jsx("input", { type: "date", value: from, min: min, max: to || max, onChange: (e) => setFrom(e.target.value), className: "num h-9 rounded-md border bg-card px-2", "aria-label": "From date" })] }), _jsxs("label", { className: "flex flex-col gap-1", children: ["To", _jsx("input", { type: "date", value: to, min: from || min, max: max, onChange: (e) => setTo(e.target.value), className: "num h-9 rounded-md border bg-card px-2", "aria-label": "To date" })] }), _jsxs("span", { className: "text-muted-foreground", children: ["Data available ", min ? `${min} to ${max}` : "n/a"] })] }), _jsxs(Tabs, { defaultValue: "vitals", children: [_jsxs(TabsList, { children: [_jsx(TabsTrigger, { value: "vitals", children: "Vitals" }), _jsx(TabsTrigger, { value: "routine", children: "Routine" })] }), _jsx(TabsContent, { value: "vitals", className: "mt-3", children: vit.isLoading || !v ? (vit.isError ? _jsx(ErrorNote, { error: vit.error }) : _jsxs("div", { className: "grid gap-3 md:grid-cols-2", children: [_jsx(CardSkeleton, { h: "h-60" }), _jsx(CardSkeleton, { h: "h-60" })] })) : (_jsxs("div", { className: "grid gap-3 md:grid-cols-2", "data-testid": "vitals-charts", children: [_jsx(VitalChart, { title: "Blood pressure", unit: "mmHg", start: v.start, end: v.end, series: [{ s: v.series.sys, color: "var(--chart-1)", name: "Systolic" }, { s: v.series.dia, color: "var(--chart-2)", name: "Diastolic" }] }), _jsx(VitalChart, { title: "Heart rate", unit: "bpm", start: v.start, end: v.end, series: [{ s: v.series.hr, color: "var(--chart-1)", name: "Heart rate" }] }), _jsx(VitalChart, { title: "Temperature", unit: "\u00B0C", start: v.start, end: v.end, series: [{ s: v.series.temp, color: "var(--chart-1)", name: "Temperature" }] }), _jsx(VitalChart, { title: "Weight", unit: "kg", start: v.start, end: v.end, series: [{ s: v.series.weight, color: "var(--chart-1)", name: "Weight" }] })] })) }), _jsx(TabsContent, { value: "routine", className: "mt-3 flex flex-col gap-3", children: !rou.data ? (rou.isError ? _jsx(ErrorNote, { error: rou.error }) : _jsx(CardSkeleton, { h: "h-72" })) : (_jsxs(_Fragment, { children: [_jsxs("p", { className: "flex items-start gap-1.5 text-sm text-muted-foreground", children: [_jsx(Info, { className: "mt-0.5 size-4 shrink-0", "aria-hidden": "true" }), "These charts describe unusual patterns against this person's own usual range. They are not a prediction."] }), _jsx(Heatmap, { r: rou.data }), _jsxs("div", { className: "grid gap-3 md:grid-cols-2", "data-testid": "routine-charts", children: [_jsx(BandBars, { title: "Night activity", unit: "events per night", k: "night_events", days: rou.data.days, note: "personal baseline not yet established" }), _jsx(BandBars, { title: "Night bathroom visits", unit: "visits per night", k: "night_bathroom", days: rou.data.days, note: "personal baseline not yet established" }), _jsx(BandBars, { title: "Total activity", unit: "events per day", k: "total_events", days: rou.data.days, note: "personal baseline not yet established" })] })] })) })] })] }));
}
