import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Bot, Cpu, Settings2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { CardSkeleton, ErrorNote } from "@/components/States";
import { ago, personLabel, useNow } from "@/lib/live";
import { useActivity, useAgentSummary, useAlertList, usePatients } from "@/lib/queries";
import { cn } from "@/lib/utils";
const KIND_CLS = {
    detector: "bg-gap-bg text-gap-fg border-gap-line",
    agent: "bg-normal-bg text-normal-fg border-normal-line",
    infra: "bg-secondary text-secondary-foreground border-border",
};
const KIND_ICON = { detector: Cpu, agent: Bot, infra: Settings2 };
const STATUS_CLS = { running: "text-normal-fg", idle: "text-muted-foreground", missing: "text-urgent-fg", stopped: "text-urgent-fg", "n/a": "text-muted-foreground" };
const KIND_OF = { quality: "detector", health_monitoring: "detector", fall: "detector", dispatcher: "infra", supervisor: "infra", watchdog: "infra" };
const NAME = { quality: "Data Quality", health_monitoring: "Vitals + Behaviour", fall: "Fall detector", triage: "Triage", dispatcher: "Alert dispatcher", investigator: "Case Investigator", coordinator: "Care Coordinator", report: "Report agent", supervisor: "Supervisor", watchdog: "Watchdog" };
const clock = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
function Chip({ agent }) {
    return _jsx("span", { className: cn("shrink-0 rounded-full border px-2 py-0.5 text-xs font-semibold", KIND_CLS[KIND_OF[agent] ?? "agent"]), children: NAME[agent] ?? agent });
}
function AgentCard({ a, active, onPick, now }) {
    const Icon = KIND_ICON[a.kind] ?? Bot;
    return (_jsx("li", { children: _jsxs("button", { type: "button", onClick: onPick, "aria-pressed": active, "data-testid": `agent-card-${a.id}`, className: cn("flex h-full w-full flex-col gap-1 rounded-xl border bg-card p-3 text-left text-sm transition-shadow hover:shadow-md", active && "ring-2 ring-primary"), children: [_jsxs("span", { className: "flex items-center justify-between gap-2", children: [_jsxs("span", { className: "flex items-center gap-1.5 font-semibold", children: [_jsx(Icon, { className: "size-4", "aria-hidden": "true" }), a.name] }), _jsx("span", { className: cn("text-xs font-semibold", STATUS_CLS[a.status]), "data-testid": "agent-status", children: a.status })] }), _jsxs("span", { className: "text-xs text-muted-foreground", children: [a.actions, " action", a.actions === 1 ? "" : "s", " logged", a.last_ts ? ` · last ${ago(now - a.last_ts)}` : ""] }), _jsx("span", { className: "line-clamp-2 text-xs", children: a.last_text ?? a.detail ?? "Nothing logged yet." })] }) }));
}
function EventRow({ e, now, rel, t0 }) {
    return (_jsxs("li", { className: "flex flex-wrap items-start gap-x-2 gap-y-1 border-b py-2 text-sm last:border-b-0", "data-testid": "activity-row", "data-agent": e.agent, children: [_jsx("span", { className: "num w-20 shrink-0 text-xs text-muted-foreground", title: new Date(e.ts * 1000).toLocaleString(), children: rel && t0 != null ? `+${(e.ts - t0).toFixed(1)} s` : clock(e.ts) }), _jsx(Chip, { agent: e.agent }), _jsxs("span", { className: "min-w-0 flex-1", children: [e.text, !rel && e.patient_id && _jsxs(_Fragment, { children: [" \u00B7 ", _jsx(Link, { to: `/patients/${e.patient_id}`, className: "underline", children: personLabel(e.patient_id) }), e.day ? ` · ${e.day}` : ""] }), !rel && _jsxs("span", { className: "text-xs text-muted-foreground", children: [" (", ago(now - e.ts), ")"] })] })] }));
}
export default function AgentActivity() {
    const now = useNow(2000);
    const [agent, setAgent] = useState();
    const [sel, setSel] = useState("");
    const sum = useAgentSummary();
    const feed = useActivity({ agent, limit: 80 });
    const patients = usePatients();
    const alerts = useAlertList(30);
    const cases = useMemo(() => {
        const m = new Map();
        for (const a of alerts.data?.items ?? []) {
            if (!a.event_time)
                continue;
            const d = a.event_time.slice(0, 10);
            m.set(`${a.patient_id}|${d}`, `${personLabel(a.patient_id)} · ${d} · ${a.type === "fall" ? "fall alert" : "critical vitals alert"}`);
        }
        for (const p of patients.data?.items ?? [])
            if (p.latest_day && p.latest_status && p.latest_status !== "NORMAL" && !m.has(`${p.patient_id}|${p.latest_day}`))
                m.set(`${p.patient_id}|${p.latest_day}`, `${personLabel(p.patient_id)} · ${p.latest_day} · ${p.latest_status.toLowerCase()} day`);
        return [...m.entries()];
    }, [alerts.data, patients.data]);
    const chosen = sel || cases[0]?.[0] || "";
    const [cp, cd] = chosen.split("|");
    const flow = useActivity({ pid: cp || undefined, day: cd || undefined, oldestFirst: true, limit: 200 });
    const t0 = flow.data?.[0]?.ts;
    return (_jsxs("div", { className: "flex flex-col gap-5", children: [_jsxs("div", { children: [_jsx("h2", { className: "text-xl font-semibold", children: "Agent activity" }), _jsx("p", { className: "max-w-3xl text-sm text-muted-foreground", children: "What each detector and agent has done, taken from the records they write (not a separate log). Detectors sense, agents decide and act, infrastructure delivers and restarts. Nothing here can change what they do." })] }), sum.isError && _jsx(ErrorNote, { error: sum.error }), _jsxs("section", { "aria-labelledby": "who", className: "flex flex-col gap-2", children: [_jsxs("h2", { id: "who", className: "text-lg font-semibold", children: ["Who is doing what ", _jsx("span", { className: "text-sm font-normal text-muted-foreground", children: "(select one to filter the timeline)" })] }), !sum.data ? _jsx(CardSkeleton, { h: "h-32" }) : _jsx("ul", { className: "grid gap-2 sm:grid-cols-2 lg:grid-cols-5", "data-testid": "agent-cards", children: sum.data.map((a) => _jsx(AgentCard, { a: a, now: now, active: agent === a.id, onPick: () => setAgent(agent === a.id ? undefined : a.id) }, a.id)) })] }), _jsxs(Card, { children: [_jsx(CardHeader, { children: _jsx(CardTitle, { children: "Follow one case" }) }), _jsx(CardContent, { className: "flex flex-col gap-3", children: !cases.length ? _jsx("p", { className: "text-sm text-muted-foreground", "data-testid": "flow-empty", children: "No alert or changed day yet. When one appears, pick it here to see each hand-over from detector to report in order." }) : (_jsxs(_Fragment, { children: [_jsxs("label", { className: "flex flex-wrap items-center gap-2 text-sm", children: [_jsx("span", { className: "font-medium", children: "Case" }), _jsx("select", { className: "rounded-md border bg-card px-2 py-1.5", value: chosen, onChange: (e) => setSel(e.target.value), "data-testid": "case-select", "aria-label": "Case to follow", children: cases.map(([k, label]) => _jsx("option", { value: k, children: label }, k)) })] }), flow.isError && _jsx(ErrorNote, { error: flow.error }), !flow.data ? _jsx(CardSkeleton, { h: "h-24" }) : !flow.data.length ? _jsx("p", { className: "text-sm text-muted-foreground", children: "Nothing recorded for this case." }) : (_jsx("ol", { "data-testid": "flow", "aria-label": "Steps of this case, oldest first", children: flow.data.map((e, i) => _jsx(EventRow, { e: e, now: now, rel: true, t0: t0 }, i)) })), _jsx("p", { className: "text-xs text-muted-foreground", children: "Times are seconds after the first step, on this machine's clock. A step missing here means that part did not run or write a record for this case (for example, a replay made before the Case Investigator existed has no case file). In live and emulator mode the data-quality and vitals / behaviour checks run inside the live processor, so they appear as the reasons in Triage's decision rather than as separate detector steps." })] })) })] }), _jsxs(Card, { children: [_jsx(CardHeader, { children: _jsxs(CardTitle, { children: ["Latest activity", agent ? `: ${NAME[agent] ?? agent}` : ""] }) }), _jsxs(CardContent, { children: [feed.isError && _jsx(ErrorNote, { error: feed.error }), !feed.data ? _jsx(CardSkeleton, { h: "h-32" }) : !feed.data.length ? _jsx("p", { className: "text-sm text-muted-foreground", "data-testid": "feed-empty", children: agent ? "This part has not logged anything yet." : "Nothing has happened yet. Start the replay or the emulator in Demo tools." }) : _jsx("ol", { "data-testid": "feed", children: feed.data.map((e, i) => _jsx(EventRow, { e: e, now: now }, i)) })] })] })] }));
}
