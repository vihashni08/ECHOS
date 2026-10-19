import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { BookOpen, CheckCircle2, Circle } from "lucide-react";
import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import SendWeekly from "./SendWeekly";
import { RESET_TEXT } from "./SimControls";
import { useAgents, useConfig, useKill, usePatients, useSimControl, useSimFall } from "@/lib/queries";
import { isoWeek } from "@/lib/format";
function Step({ n, title, look, done, children }) {
    return (_jsxs("li", { className: "flex flex-col gap-2 rounded-lg border p-3 text-sm", "data-testid": `demo-step-${n}`, children: [_jsxs("div", { className: "flex items-start gap-2 font-semibold", children: [done ? _jsx(CheckCircle2, { className: "mt-0.5 size-4 shrink-0 text-normal-fg", "aria-label": "done" }) : _jsx(Circle, { className: "mt-0.5 size-4 shrink-0 text-muted-foreground", "aria-hidden": "true" }), n, ". ", title] }), _jsxs("p", { className: "text-muted-foreground", children: [_jsx("strong", { className: "font-medium text-foreground", children: "Look at:" }), " ", look] }), _jsx("div", { className: "flex flex-wrap items-center gap-2", children: children }), done && _jsx("p", { role: "status", className: "text-sm font-medium", children: done })] }));
}
export function DemoGuideButton() {
    const cfg = useConfig();
    const [open, setOpen] = useState(false);
    const ref = useRef(null);
    if (!cfg.data?.demo)
        return null;
    return (_jsxs(_Fragment, { children: [_jsxs(Button, { ref: ref, variant: "outline", onClick: () => setOpen(true), "data-testid": "demo-guide-button", children: [_jsx(BookOpen, { className: "size-4", "aria-hidden": "true" }), "Demo guide"] }), _jsx(DemoGuide, { open: open, onOpenChange: setOpen, returnFocus: () => ref.current?.focus() })] }));
}
function DemoGuide({ open, onOpenChange, returnFocus }) {
    const nav = useNavigate();
    const cfg = useConfig();
    const patients = usePatients();
    const agents = useAgents();
    const ctl = useSimControl();
    const fall = useSimFall();
    const kill = useKill();
    const [done, setDone] = useState({});
    const [confirmReset, setConfirmReset] = useState(false);
    const mark = (k, v) => setDone((d) => ({ ...d, [k]: v }));
    const first = patients.data?.items[0];
    const latest = patients.data?.items.filter((p) => p.latest_day).sort((a, b) => (b.latest_day ?? "").localeCompare(a.latest_day ?? ""))[0];
    const go = (to) => { onOpenChange(false); nav(to); };
    const resetAndPlay = async () => {
        try {
            await ctl.mutateAsync({ action: "reset" });
            await ctl.mutateAsync({ action: "speed", speed: 5 });
            await ctl.mutateAsync({ action: "play" });
            mark("1", "Simulation reset and playing at 5 events/s.");
        }
        catch (e) {
            mark("1", `Could not start: ${e.message}`);
        }
    };
    return (_jsx(Sheet, { open: open, onOpenChange: onOpenChange, children: _jsxs(SheetContent, { side: "right", className: "w-full overflow-y-auto sm:max-w-md", "data-testid": "demo-guide", onCloseAutoFocus: (e) => { e.preventDefault(); returnFocus(); }, children: [_jsxs(SheetHeader, { children: [_jsx(SheetTitle, { children: "Demo guide" }), _jsx(SheetDescription, { children: "Five steps, run against the live API. Everything is simulated and mail is dry run." })] }), _jsxs("ol", { className: "flex flex-col gap-3 px-4 pb-6", children: [_jsx(Step, { n: 1, title: "Reset and play", look: "the top bar changes to 'Simulation: playing' and the Dashboard cards fill with day statuses.", done: done["1"], children: _jsx(Button, { onClick: () => setConfirmReset(true), disabled: ctl.isPending, "data-testid": "demo-reset-play", children: "Reset and play" }) }), _jsx(Step, { n: 2, title: "Jump to a day", look: "the Caregiver page shows that day's report, the 7-day strip and 'How this report was made'.", done: done["2"], children: _jsx(Button, { variant: "outline", disabled: !latest, onClick: () => { mark("2", `Opened ${latest.patient_id}, ${latest.latest_day}.`); go(`/caregiver?patient=${latest.patient_id}&day=${latest.latest_day}`); }, "data-testid": "demo-jump", children: "Open the latest day" }) }), _jsxs(Step, { n: 3, title: "Inject a fall", look: "the Fall Monitor shows the waveform, the 'Simulated fall signal' badge and the Alert sent panel with two recipients; the Dashboard feed gets an URGENT alert.", done: done["3"], children: [_jsx(Button, { variant: "outline", disabled: !first || fall.isPending, "data-testid": "demo-fall", onClick: () => fall.mutate({ patient_id: first.patient_id, activity: "F02", subject: "SA03" }, {
                                        onSuccess: (r) => { mark("3", `Injected a SisFall ${r.activity} window (subject ${r.sisfall_subject}) for ${r.patient_id}.`); go(`/falls?patient=${r.patient_id}${r.event_id != null ? `&fall=${r.event_id}` : ""}`); },
                                        onError: (e) => mark("3", `Could not inject: ${e.message}`)
                                    }), children: fall.isPending ? "Injecting…" : "Inject a fall" }), _jsx("span", { className: "text-xs text-muted-foreground", children: "Uses fall type F02 from held-out subject SA03: all 5 of its F02 recordings are detected and followed by confirmed inactivity, so the demo always raises the alert (the Fall Monitor button picks a random type and subject, and some of those are honestly missed)." })] }), _jsxs(Step, { n: 4, title: "Kill an agent and watch recovery", look: "the agent strip on the Dashboard (or System, Health): the Fall agent's heartbeat age grows, then it is alive again. A fall injected meanwhile still alerts, a little later.", done: done["4"], children: [_jsx(Button, { variant: "outline", disabled: !cfg.data?.allow_kill || kill.isPending || !agents.data, "data-testid": "demo-kill", onClick: () => { mark("4", ""); kill.mutate("fall", { onSuccess: (r) => mark("4", r.recovered ? `Fall agent alive again after ${r.recovery_s} s.` : "The Fall agent did not recover in time."), onError: (e) => mark("4", e.message) }); }, children: kill.isPending ? "Waiting for recovery…" : "Kill the Fall agent" }), !cfg.data?.allow_kill && _jsx("span", { className: "text-xs text-muted-foreground", children: "Needs the API started with --demo." })] }), _jsxs(Step, { n: 5, title: "Send the weekly report", look: "the Clinician page: the outbox list shows a new row with the PDF name, status SENT and channel 'Dry run'.", done: done["5"], children: [latest ? _jsx(SendWeekly, { pid: latest.patient_id, week: isoWeek(latest.latest_day), label: "Send the weekly report", variant: "outline" }) : _jsx("span", { className: "text-xs text-muted-foreground", children: "Run step 1 first." }), latest && _jsx(Button, { variant: "ghost", onClick: () => go(`/clinician?patient=${latest.patient_id}&week=${isoWeek(latest.latest_day)}`), children: "Open Clinician" })] })] }), _jsx(AlertDialog, { open: confirmReset, onOpenChange: setConfirmReset, children: _jsxs(AlertDialogContent, { children: [_jsxs(AlertDialogHeader, { children: [_jsx(AlertDialogTitle, { children: "Reset the simulation?" }), _jsx(AlertDialogDescription, { children: RESET_TEXT })] }), _jsxs(AlertDialogFooter, { children: [_jsx(AlertDialogCancel, { children: "Cancel" }), _jsx(AlertDialogAction, { onClick: resetAndPlay, "data-testid": "demo-confirm-reset", children: "Reset and clear" })] })] }) })] }) }));
}
