import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState } from "react";
import { Pause, Play, Zap } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useEmulator, usePatients } from "@/lib/queries";
import { useMode } from "@/lib/live";
const SPEEDS = [[600, "1 data hour = 6 s"], [3600, "1 data hour = 1 s"], [14400, "1 data hour = 0.25 s"]];
/** The labelled device emulator: start / pause / pace / inject a synthetic fall. */
export default function EmulatorControls() {
    const { live } = useMode();
    const emu = useEmulator();
    const patients = usePatients();
    const [pid, setPid] = useState("");
    const [msg, setMsg] = useState("");
    const e = live.data?.emulator;
    const pats = e?.patients ?? [];
    const target = pid || pats[0] || patients.data?.items[0]?.patient_id || "";
    const run = (body, ok) => {
        setMsg("");
        emu.mutate(body, { onSuccess: (r) => ok && setMsg(ok(r)), onError: (er) => setMsg(er.message) });
    };
    const playing = e?.status === "playing";
    return (_jsxs("section", { className: "flex flex-col gap-3", "aria-label": "Device emulator", "data-testid": "emulator-controls", children: [_jsx("p", { className: "text-sm text-muted-foreground", children: "The emulator replays real TIHM room-sensor and vitals events in wall-clock time. It is a stand-in for a device, not a device." }), _jsxs("div", { className: "flex flex-wrap items-center gap-2", children: [_jsxs(Button, { onClick: () => run({ action: playing ? "pause" : "play" }), disabled: emu.isPending || e?.status === "finished", "data-testid": "emu-play", children: [playing ? _jsx(Pause, { className: "size-4", "aria-hidden": "true" }) : _jsx(Play, { className: "size-4", "aria-hidden": "true" }), playing ? "Pause" : e?.status === "paused" ? "Resume" : "Start"] }), _jsxs(Select, { value: String(e?.speed ?? 3600), onValueChange: (v) => run({ action: "speed", speed: Number(v) }), disabled: !e, children: [_jsx(SelectTrigger, { className: "w-48", "aria-label": "Emulator pace", children: _jsx(SelectValue, {}) }), _jsx(SelectContent, { children: SPEEDS.map(([v, l]) => _jsx(SelectItem, { value: String(v), children: l }, v)) })] })] }), _jsx("p", { className: "num text-sm", "data-testid": "emu-status", children: !e ? "Emulator not loaded yet" : `${e.status} · data time ${e.sim_now?.replace("T", " ").slice(0, 16) ?? "n/a"} · ${e.cursor}/${e.total} events` }), _jsxs("div", { className: "flex flex-wrap items-center gap-2 border-t pt-3", children: [_jsxs(Select, { value: target, onValueChange: setPid, children: [_jsx(SelectTrigger, { className: "w-40", "aria-label": "Patient for the fall", children: _jsx(SelectValue, { placeholder: "Patient" }) }), _jsx(SelectContent, { children: (pats.length ? pats : patients.data?.items.map((p) => p.patient_id) ?? []).map((p) => _jsxs(SelectItem, { value: p, children: ["Patient ", p] }, p)) })] }), _jsxs(Button, { variant: "outline", onClick: () => run({ action: "fall", patient_id: target }, (r) => r.note ?? "Fall injected"), disabled: emu.isPending || !target || !playing && e?.status !== "paused", "data-testid": "emu-fall", children: [_jsx(Zap, { className: "size-4", "aria-hidden": "true" }), "Inject a fall now"] })] }), _jsx("p", { className: "text-xs text-muted-foreground", children: "SYNTHETIC: a SisFall recording from a held-out subject (young volunteers on mats), written as one accelerometer event at the patient's current data time." }), msg && _jsx("p", { role: "status", className: "rounded-md border px-3 py-2 text-sm", "data-testid": "emu-msg", children: msg })] }));
}
