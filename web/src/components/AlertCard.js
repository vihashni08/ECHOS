import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { Check, ChevronRight, ShieldCheck, Siren, TimerReset } from "lucide-react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { fmtTime, typeLabel } from "@/lib/format";
import { mmss, personLabel, useNow } from "@/lib/live";
import { useAck, useResolve } from "@/lib/queries";
import { cn } from "@/lib/utils";
export const STATE_TEXT = {
    NEW: "Being delivered", SENT: "Delivered: waiting for someone to acknowledge", ACKNOWLEDGED: "Acknowledged", RESOLVED: "Resolved", ESCALATED: "Escalated: not acknowledged in time", EXPIRED: "Expired: too old to act on (history)",
};
// EXPIRED = too old to act on (history after a restart or a replay): shown as history, never as "needs action now"
export const isOpen = (a) => a.state !== "ACKNOWLEDGED" && a.state !== "RESOLVED" && a.state !== "EXPIRED";
export function AlertCard({ a, compact }) {
    const now = useNow();
    const ack = useAck();
    const res = useResolve();
    const left = a.deadline_ts ? a.deadline_ts - now : null;
    const st = a.state ?? "NEW";
    const open = isOpen(a);
    return (_jsxs("article", { "data-testid": "alert-card", "data-state": st, "aria-label": `${typeLabel(a.type)} for patient ${a.patient_id}`, className: cn("flex flex-col gap-2 rounded-xl border p-3 text-sm", open ? "border-urgent-line bg-urgent-bg text-urgent-fg" : "bg-card"), children: [_jsxs("div", { className: "flex flex-wrap items-start justify-between gap-2", children: [_jsxs("div", { className: "flex items-start gap-2", children: [_jsx(Siren, { className: "mt-0.5 size-5 shrink-0", "aria-hidden": "true" }), _jsxs("div", { children: [_jsxs("div", { className: "text-base font-semibold", children: [typeLabel(a.type), " \u00B7 ", _jsx(Link, { to: `/patients/${a.patient_id}`, className: "num underline", children: personLabel(a.patient_id) })] }), _jsxs("div", { className: "num opacity-80", children: [fmtTime(a.event_time), " (data time)"] })] })] }), _jsxs("span", { className: "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-semibold", "data-testid": "alert-state", children: [st === "ACKNOWLEDGED" || st === "RESOLVED" ? _jsx(ShieldCheck, { className: "size-3.5", "aria-hidden": "true" }) : _jsx(TimerReset, { className: "size-3.5", "aria-hidden": "true" }), st] })] }), _jsxs("p", { children: [STATE_TEXT[st] ?? st, st === "SENT" && left != null && _jsxs(_Fragment, { children: [" \u00B7 escalates to the second contact in ", _jsx("span", { className: "num font-semibold", "data-testid": "countdown", children: mmss(left) })] }), st === "ACKNOWLEDGED" && a.ack_by && _jsxs(_Fragment, { children: [" by ", a.ack_by] }), a.note ? _jsxs(_Fragment, { children: [" \u00B7 ", a.note] }) : null] }), !compact && a.status === "FAILED" && _jsx("p", { className: "font-medium", children: "E-mail delivery failed: check the Alerts page." }), _jsxs("div", { className: "flex flex-wrap gap-2", children: [open && _jsxs(Button, { size: "sm", onClick: () => ack.mutate({ id: a.alert_id }), disabled: ack.isPending, "data-testid": "ack", children: [_jsx(Check, { className: "size-4", "aria-hidden": "true" }), "I have seen this: acknowledge"] }), st === "ACKNOWLEDGED" && _jsx(Button, { size: "sm", variant: "outline", onClick: () => res.mutate({ id: a.alert_id, note: "handled" }), disabled: res.isPending, "data-testid": "resolve", children: "Mark as handled" }), _jsx(Button, { size: "sm", variant: "outline", asChild: true, children: _jsxs(Link, { to: `/patients/${a.patient_id}`, children: ["Open case", _jsx(ChevronRight, { className: "size-4", "aria-hidden": "true" })] }) })] })] }));
}
