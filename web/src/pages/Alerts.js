import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState } from "react";
import { Link } from "react-router-dom";
import { AlertCard, isOpen } from "@/components/AlertCard";
import { Empty, ErrorNote } from "@/components/States";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useAlertList, useOutbox, useTasks } from "@/lib/queries";
import { channelLabel, fmtEpoch, fmtTime } from "@/lib/format";
import { personLabel } from "@/lib/live";
const ROLE = { caregiver: "Caregiver", clinician: "Clinician", escalation: "Second contact (escalation)" };
export default function Alerts() {
    const a = useAlertList(100);
    const o = useOutbox(30);
    const t = useTasks(undefined);
    const [tab, setTab] = useState("open");
    const rows = (a.data?.items ?? []).filter((x) => tab === "all" || isOpen(x));
    return (_jsxs("div", { className: "flex flex-col gap-5", children: [_jsxs("div", { children: [_jsx("h1", { className: "text-2xl font-semibold", children: "Alerts" }), _jsx("p", { className: "max-w-3xl text-sm text-muted-foreground", children: "An alert is sent at once to the caregiver and the clinician. The Care Coordinator then waits for someone to acknowledge it; if no one does in time, the same alert goes to a second contact. Nothing here is a diagnosis." })] }), _jsx("div", { role: "tablist", "aria-label": "Alert filter", className: "flex gap-2", children: ["open", "all"].map((k) => _jsx("button", { role: "tab", "aria-selected": tab === k, onClick: () => setTab(k), "data-testid": `tab-${k}`, className: `rounded-md border px-3 py-1.5 text-sm font-medium ${tab === k ? "bg-accent text-accent-foreground" : "bg-card"}`, children: k === "open" ? "Waiting for action" : "All alerts" }, k)) }), a.isError && _jsx(ErrorNote, { error: a.error }), rows.length === 0 ? _jsx(Empty, { title: tab === "open" ? "No alert is waiting" : "No alerts yet", children: "Fall and critical-vitals alerts appear here the moment they are raised." })
                : _jsx("div", { className: "flex flex-col gap-2", "data-testid": "alert-list", children: rows.map((x) => _jsx(AlertCard, { a: x }, x.alert_id)) }), _jsxs("div", { className: "grid gap-4 lg:grid-cols-2", children: [_jsxs(Card, { children: [_jsx(CardHeader, { children: _jsx(CardTitle, { children: "Care tasks" }) }), _jsx(CardContent, { children: !t.data?.items.length ? _jsx("p", { className: "text-sm text-muted-foreground", children: "No tasks yet." }) : (_jsx("ul", { className: "flex flex-col gap-2 text-sm", children: t.data.items.map((x) => _jsxs("li", { className: "flex flex-wrap justify-between gap-2 rounded-lg border p-2.5", children: [_jsxs("span", { children: [_jsx("span", { className: "font-medium", children: x.title }), " \u00B7 ", _jsx(Link, { className: "underline", to: `/patients/${x.patient_id}`, children: personLabel(x.patient_id) })] }), _jsx("span", { className: "font-semibold", children: x.status })] }, x.id)) })) })] }), _jsxs(Card, { children: [_jsx(CardHeader, { children: _jsx(CardTitle, { children: "Delivery log" }) }), _jsx(CardContent, { children: !o.data?.items.length ? _jsx("p", { className: "text-sm text-muted-foreground", children: "Nothing sent yet." }) : (_jsx("ul", { className: "flex flex-col gap-2 text-sm", "data-testid": "delivery-log", children: o.data.items.slice(0, 12).map((x) => _jsxs("li", { className: "rounded-lg border p-2.5", children: [_jsxs("div", { className: "font-medium", children: [ROLE[x.recipient_role] ?? x.recipient_role, " \u00B7 ", x.status] }), _jsxs("div", { className: "num text-muted-foreground", children: [x.address, " \u00B7 ", channelLabel(x.channel), " \u00B7 ", x.sent_time ? fmtEpoch(x.sent_time) : fmtTime(x.event_time)] })] }, x.outbox_id)) })) })] })] })] }));
}
