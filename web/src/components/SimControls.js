import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState } from "react";
import { Pause, Play, RotateCcw } from "lucide-react";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { usePatients, useSimControl, useSimState } from "@/lib/queries";
const SPEEDS = [0.5, 1, 5, 20, 100];
export const RESET_TEXT = "This clears the simulation tables of the database the API was started with: events, agent outputs, day statuses, alerts, outbox rows, daily facts and reports. Patient demographics, models and report files on disk are kept.";
export default function SimControls() {
    const sim = useSimState();
    const patients = usePatients();
    const ctl = useSimControl();
    const [confirmReset, setConfirmReset] = useState(false);
    const [needReset, setNeedReset] = useState(false);
    const [msg, setMsg] = useState("");
    const s = sim.data;
    const playing = s?.status === "playing";
    const storeHasData = (patients.data?.items ?? []).some((p) => p.latest_day);
    const run = (body) => { setMsg(""); ctl.mutate(body, { onError: (e) => setMsg(e.message) }); };
    const onPlay = () => (s?.status === "idle" && (s.cursor ?? 0) === 0 && storeHasData ? setNeedReset(true) : run({ action: "play" }));
    const resetText = RESET_TEXT;
    return (_jsxs(Card, { children: [_jsx(CardHeader, { children: _jsx(CardTitle, { children: "Simulation" }) }), _jsxs(CardContent, { className: "flex flex-col gap-3", children: [_jsxs("div", { className: "flex flex-wrap items-center gap-2", children: [_jsxs(Button, { onClick: playing ? () => run({ action: "pause" }) : onPlay, disabled: !s || ctl.isPending || s.status === "finished", "aria-label": playing ? "Pause simulation" : "Play simulation", "data-testid": "play-pause", children: [playing ? _jsx(Pause, { className: "size-4", "aria-hidden": "true" }) : _jsx(Play, { className: "size-4", "aria-hidden": "true" }), playing ? "Pause" : "Play"] }), _jsxs(Select, { value: String(s?.speed ?? 20), onValueChange: (v) => run({ action: "speed", speed: Number(v) }), disabled: !s, children: [_jsx(SelectTrigger, { className: "w-32", "aria-label": "Simulation speed", children: _jsx(SelectValue, {}) }), _jsx(SelectContent, { children: [...new Set([...SPEEDS, s?.speed ?? 20])].sort((a, b) => a - b).map((v) => _jsxs(SelectItem, { value: String(v), children: [v, " events/s"] }, v)) })] }), _jsxs(Button, { variant: "outline", onClick: () => setConfirmReset(true), disabled: !s || ctl.isPending, "aria-label": "Reset simulation", "data-testid": "reset", children: [_jsx(RotateCcw, { className: "size-4", "aria-hidden": "true" }), "Reset"] })] }), _jsx("p", { className: "text-sm text-muted-foreground", children: !s ? "Loading…" : s.status === "idle" ? `Idle: ${s.total ?? 0} events ready to replay.` : s.status === "playing" ? `Replaying: ${s.cursor} of ${s.total} events.` : s.status === "paused" ? `Paused at ${s.cursor} of ${s.total} events.` : "" }), s?.status === "finished" && _jsx("p", { className: "text-sm text-muted-foreground", children: "All events were emitted. Reset to run again." }), msg && _jsx("p", { role: "alert", className: "rounded-md border border-monitor-line bg-monitor-bg px-3 py-2 text-sm text-monitor-fg", children: msg })] }), _jsx(AlertDialog, { open: confirmReset, onOpenChange: setConfirmReset, children: _jsxs(AlertDialogContent, { children: [_jsxs(AlertDialogHeader, { children: [_jsx(AlertDialogTitle, { children: "Reset the simulation?" }), _jsx(AlertDialogDescription, { children: resetText })] }), _jsxs(AlertDialogFooter, { children: [_jsx(AlertDialogCancel, { children: "Cancel" }), _jsx(AlertDialogAction, { onClick: () => run({ action: "reset" }), children: "Reset and clear" })] })] }) }), _jsx(AlertDialog, { open: needReset, onOpenChange: setNeedReset, children: _jsxs(AlertDialogContent, { children: [_jsxs(AlertDialogHeader, { children: [_jsx(AlertDialogTitle, { children: "A reset is needed before playing" }), _jsxs(AlertDialogDescription, { children: ["The store already holds events from an earlier run, so the simulation cannot start from the beginning. ", resetText] })] }), _jsxs(AlertDialogFooter, { children: [_jsx(AlertDialogCancel, { children: "Keep the data" }), _jsx(AlertDialogAction, { onClick: () => ctl.mutate({ action: "reset" }, { onSuccess: () => ctl.mutate({ action: "play" }) }), children: "Reset and play" })] })] }) })] }));
}
