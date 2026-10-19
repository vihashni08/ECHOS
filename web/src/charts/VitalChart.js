import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { AlertTriangle } from "lucide-react";
import { Area, CartesianGrid, ComposedChart, Legend, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis } from "recharts";
const DAY = 86400000;
const ts = (s) => new Date(s.replace(" ", "T")).getTime();
const fmtX = (v) => new Date(v).toISOString().slice(5, 10);
function FlagShape(p) {
    const { cx = 0, cy = 0 } = p;
    return _jsx("path", { d: `M${cx} ${cy - 8} L${cx + 8} ${cy} L${cx} ${cy + 8} L${cx - 8} ${cy} Z`, fill: "var(--urgent-bg)", stroke: "var(--urgent-line)", strokeWidth: 2 });
}
function Tip({ active, payload, unit }) {
    const p = payload?.find((q) => q.payload.y !== undefined)?.payload;
    if (!active || !p)
        return null;
    return _jsxs("div", { className: "rounded-md border bg-popover px-2.5 py-1.5 text-xs text-popover-foreground shadow", children: [_jsx("div", { className: "num", children: new Date(p.x).toISOString().slice(0, 16).replace("T", " ") }), _jsxs("div", { className: "num font-semibold", children: [p.y, " ", unit, p.flagged ? " (outside personal band)" : ""] })] });
}
/** One vital (or a pair, e.g. systolic + diastolic) against the past-only personal band; flagged readings get a diamond marker. */
export default function VitalChart({ title, unit, series, start, end }) {
    const x0 = new Date(start).getTime(), x1 = new Date(end).getTime() + DAY;
    const established = series.some((q) => q.s.baseline_established);
    const nPts = series.reduce((n, q) => n + q.s.points.length, 0);
    const bands = series.map((q) => q.s.bands.flatMap((b) => [{ x: new Date(b.day).getTime(), band: [b.lo, b.hi] }, { x: new Date(b.day).getTime() + DAY - 1, band: [b.lo, b.hi] }]));
    const allY = [...series.flatMap((q) => q.s.points.map((p) => p.value)), ...series.flatMap((q) => q.s.bands.flatMap((b) => [b.lo, b.hi]))];
    const pad = allY.length ? Math.max((Math.max(...allY) - Math.min(...allY)) * 0.12, 1) : 1;
    const yDomain = allY.length ? [Math.floor(Math.min(...allY) - pad), Math.ceil(Math.max(...allY) + pad)] : [0, 1];
    const summary = series.map((q) => {
        const v = q.s.points.map((p) => p.value);
        return v.length ? `${q.name}: ${v.length} readings, ${Math.min(...v)} to ${Math.max(...v)} ${unit}, ${q.s.points.filter((p) => p.flagged).length} outside the personal band` : `${q.name}: no readings`;
    }).join(". ");
    return (_jsxs("figure", { className: "flex flex-col gap-1.5 rounded-lg border bg-card p-3", "aria-label": `${title} chart`, children: [_jsxs("figcaption", { className: "flex items-center justify-between text-sm font-semibold", children: [title, !established && _jsx("span", { className: "text-xs font-normal text-muted-foreground", children: "personal baseline not yet established" })] }), nPts === 0 ? _jsx("div", { className: "flex h-24 items-center justify-center text-sm text-muted-foreground", children: "No readings in this period" }) : (_jsx("div", { className: "h-56 w-full", role: "img", "aria-label": summary, children: _jsx(ResponsiveContainer, { children: _jsxs(ComposedChart, { margin: { top: 6, right: 8, bottom: 18, left: 0 }, children: [_jsx(CartesianGrid, { stroke: "var(--border)", strokeDasharray: "3 3" }), _jsx(XAxis, { type: "number", dataKey: "x", domain: [x0, x1], scale: "time", tickFormatter: fmtX, tick: { fontSize: 11, fill: "var(--muted-foreground)" }, label: { value: "Date (month-day)", position: "insideBottom", offset: -12, fontSize: 11, fill: "var(--muted-foreground)" } }), _jsx(YAxis, { type: "number", dataKey: "y", domain: yDomain, allowDataOverflow: true, width: 42, tick: { fontSize: 11, fill: "var(--muted-foreground)" }, label: { value: unit, angle: -90, position: "insideLeft", fontSize: 11, fill: "var(--muted-foreground)" } }), _jsx(Tooltip, { content: _jsx(Tip, { unit: unit }) }), _jsx(Legend, { verticalAlign: "top", height: 22, iconSize: 10, wrapperStyle: { fontSize: 11 } }), series.map((q, i) => bands[i].length > 0 && _jsx(Area, { data: bands[i], dataKey: "band", xAxisId: 0, yAxisId: 0, name: `${q.name} personal band`, stroke: "none", fill: q.color, fillOpacity: 0.16, isAnimationActive: false, legendType: "none" }, `b${i}`)), series.map((q) => _jsx(Scatter, { name: q.name, data: q.s.points.filter((p) => !p.flagged).map((p) => ({ x: ts(p.t), y: p.value })), fill: q.color, isAnimationActive: false }, q.name)), series.some((q) => q.s.points.some((p) => p.flagged)) && (_jsx(Scatter, { name: "Flagged (outside band)", legendType: "diamond", shape: _jsx(FlagShape, {}), isAnimationActive: false, data: series.flatMap((q) => q.s.points.filter((p) => p.flagged).map((p) => ({ x: ts(p.t), y: p.value, flagged: true }))), fill: "var(--urgent-line)" }))] }) }) })), _jsx("p", { className: "text-xs text-muted-foreground", children: "Shaded band: this person's usual range (past readings only)." }), _jsxs("p", { className: "flex items-start gap-1.5 text-xs text-muted-foreground", children: [series.some((q) => q.s.points.some((p) => p.flagged)) && _jsx(AlertTriangle, { className: "mt-0.5 size-3.5 shrink-0", "aria-hidden": "true" }), summary] })] }));
}
