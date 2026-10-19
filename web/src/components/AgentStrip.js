import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState } from "react";
import { CheckCircle2, AlertTriangle, Skull } from "lucide-react";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useAgents, useConfig, useKill } from "@/lib/queries";
const NAME = { quality: "Data quality", health_monitoring: "Health monitoring", fall: "Fall" };
export default function AgentStrip() {
    const agents = useAgents();
    const cfg = useConfig();
    const kill = useKill();
    const [target, setTarget] = useState(null);
    const [result, setResult] = useState("");
    return (_jsxs(Card, { children: [_jsx(CardHeader, { children: _jsx(CardTitle, { children: "Agents" }) }), _jsxs(CardContent, { className: "flex flex-col gap-3", children: [!agents.data ? _jsx(Skeleton, { className: "h-16 w-full" }) : (_jsx("ul", { className: "grid gap-2 sm:grid-cols-3", "data-testid": "agent-strip", children: agents.data.map((a) => (_jsxs("li", { className: "flex flex-col gap-1.5 rounded-lg border p-3 text-sm", children: [_jsx("div", { className: "font-medium", children: NAME[a.agent] ?? a.agent }), _jsxs("div", { className: `inline-flex items-center gap-1.5 font-semibold ${a.alive ? "text-normal-fg" : "text-urgent-fg"}`, children: [a.alive ? _jsx(CheckCircle2, { className: "size-4", "aria-hidden": "true" }) : _jsx(AlertTriangle, { className: "size-4", "aria-hidden": "true" }), a.alive ? "alive" : "stale", _jsx("span", { className: "num font-normal text-muted-foreground", children: a.heartbeat_age_s == null ? "no heartbeat" : `${a.heartbeat_age_s.toFixed(0)} s ago` })] }), cfg.data?.allow_kill && (_jsxs(Button, { size: "sm", variant: "outline", className: "mt-1 self-start", onClick: () => setTarget(a.agent), disabled: kill.isPending, "aria-label": `Kill ${NAME[a.agent] ?? a.agent} agent`, children: [_jsx(Skull, { className: "size-4", "aria-hidden": "true" }), " Kill agent"] }))] }, a.agent))) })), kill.isPending && _jsx("p", { className: "text-sm text-muted-foreground", role: "status", children: "Waiting for the supervisor to bring the agent back\u2026" }), result && !kill.isPending && _jsx("p", { className: "text-sm font-medium", role: "status", "data-testid": "kill-result", children: result })] }), _jsx(AlertDialog, { open: target !== null, onOpenChange: (o) => !o && setTarget(null), children: _jsxs(AlertDialogContent, { children: [_jsxs(AlertDialogHeader, { children: [_jsxs(AlertDialogTitle, { children: ["Kill the ", NAME[target ?? ""] ?? target, " agent?"] }), _jsx(AlertDialogDescription, { children: "Fault demo: the worker process is killed and the supervisor restarts it from its checkpoint (about 12 s). Events keep queueing; no alert is lost." })] }), _jsxs(AlertDialogFooter, { children: [_jsx(AlertDialogCancel, { children: "Cancel" }), _jsx(AlertDialogAction, { onClick: () => {
                                        const t = target;
                                        setResult("");
                                        kill.mutate(t, { onSuccess: (r) => setResult(r.recovered ? `${NAME[t] ?? t} agent is alive again after ${r.recovery_s} s.` : `${NAME[t] ?? t} agent did not recover in time.`), onError: (e) => setResult(e.message) });
                                    }, children: "Kill agent" })] })] }) })] }));
}
