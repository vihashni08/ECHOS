import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { CheckCircle2, Clock, FlaskConical, Mail, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import Waveform from "@/charts/Waveform";
import { Empty, ErrorNote } from "@/components/States";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { channelLabel, fmtTime } from "@/lib/format";
import { useFall, useFalls, usePatients, useSimFall } from "@/lib/queries";
import { StatusBadge } from "@/lib/status";
function Sent({ o }) {
    const I = o.status === "SENT" ? CheckCircle2 : o.status === "FAILED" ? XCircle : Clock;
    return _jsxs("span", { className: "inline-flex items-center gap-1 font-semibold", children: [_jsx(I, { className: "size-4", "aria-hidden": "true" }), o.status === "SENT" ? "Sent" : o.status === "FAILED" ? "Failed" : "Pending"] });
}
function AlertPanel({ d }) {
    return (_jsxs(Card, { "data-testid": "alert-sent", children: [_jsx(CardHeader, { children: _jsxs(CardTitle, { className: "flex items-center gap-2", children: [_jsx(Mail, { className: "size-4", "aria-hidden": "true" }), "Alert sent"] }) }), _jsx(CardContent, { className: "flex flex-col gap-3 text-sm", children: !d.alert ? _jsx("p", { className: "text-muted-foreground", children: "No alert was raised for this fall: the post-fall inactivity was not confirmed, so the day stays at Monitor." }) : (_jsxs(_Fragment, { children: [_jsx("ul", { className: "flex flex-col divide-y rounded-md border", children: d.outbox.map((o) => (_jsxs("li", { "data-testid": "recipient", className: "grid gap-x-4 gap-y-0.5 px-3 py-2 sm:grid-cols-[6rem_1fr_1fr_6rem] sm:items-center", children: [_jsx("span", { className: "font-semibold capitalize", children: o.recipient_role }), _jsx("span", { className: "num break-all", children: o.address }), _jsx("span", { children: channelLabel(o.channel) }), _jsx(Sent, { o: o })] }, o.outbox_id))) }), d.outbox[0] && (_jsxs("div", { className: "flex flex-col gap-1", children: [_jsxs("div", { children: [_jsx("span", { className: "text-muted-foreground", children: "Subject: " }), _jsx("span", { className: "font-medium", children: d.outbox[0].subject })] }), _jsx("pre", { tabIndex: 0, "aria-label": "Message body", className: "max-h-56 overflow-auto whitespace-pre-wrap rounded-md border bg-muted p-3 font-mono text-xs leading-relaxed", children: d.outbox[0].body })] }))] })) })] }));
}
function Detail({ d }) {
    const need = d.inactivity_required_s ?? 3;
    const obs = d.inactivity_observed_s ?? 0;
    const yn = (b) => (b == null ? "n/a" : b ? "yes" : "no");
    return (_jsxs("div", { className: "flex flex-col gap-4", "data-testid": "fall-detail", children: [_jsxs(Card, { children: [_jsxs(CardHeader, { className: "flex flex-row flex-wrap items-center justify-between gap-2", children: [_jsxs(CardTitle, { children: ["Fall at ", _jsx("span", { className: "num", children: fmtTime(d.ts) }), " \u00B7 patient ", _jsx("span", { className: "num", children: d.patient_id })] }), _jsxs("div", { className: "flex flex-wrap items-center gap-2", children: [_jsxs("span", { className: "inline-flex items-center gap-1.5 rounded-full border border-dashed border-primary px-2.5 py-0.5 text-sm font-semibold text-primary", "data-testid": "sim-badge", children: [_jsx(FlaskConical, { className: "size-4", "aria-hidden": "true" }), "Simulated fall signal"] }), _jsx(StatusBadge, { status: d.day_status })] })] }), _jsxs(CardContent, { className: "flex flex-col gap-4", children: [d.waveform ? _jsx(Waveform, { w: d.waveform }) : _jsx(Empty, { title: "Waveform unavailable", children: "The SisFall recording is not on this machine." }), _jsxs("div", { className: "grid gap-4 sm:grid-cols-3", children: [_jsxs("div", { children: [_jsx("div", { className: "text-xs text-muted-foreground", children: "Fall probability" }), _jsx("div", { className: "num text-3xl font-semibold", "data-testid": "prob", children: d.probability_text }), _jsxs("div", { className: "text-xs text-muted-foreground", children: ["flag threshold ", _jsx("span", { className: "num", children: d.threshold ?? 0.8 })] })] }), _jsxs("div", { children: [_jsx("div", { className: "text-xs text-muted-foreground", children: "Peak impact" }), _jsxs("div", { className: "num text-3xl font-semibold", children: [d.peak_g?.toFixed(1) ?? "n/a", _jsx("span", { className: "text-base font-normal", children: " g" })] })] }), _jsxs("div", { children: [_jsx("div", { className: "text-xs text-muted-foreground", children: "Post-fall inactivity" }), _jsx("div", { className: "mt-1 h-3 w-full overflow-hidden rounded-full bg-muted", role: "progressbar", "aria-valuemin": 0, "aria-valuemax": need, "aria-valuenow": Math.min(obs, need), "aria-label": "Observed still time against required", children: _jsx("div", { className: "h-full rounded-full bg-primary", style: { width: `${Math.min(100, (obs / need) * 100)}%` } }) }), _jsxs("div", { className: "mt-1 text-sm", children: [_jsxs("span", { className: "num", children: [obs.toFixed(1), " s"] }), " of ", _jsxs("span", { className: "num", children: [need.toFixed(1), " s"] }), " required: ", _jsx("strong", { children: d.inactivity ? "confirmed" : "not confirmed" })] })] })] })] })] }), _jsx(AlertPanel, { d: d }), _jsx(Card, { children: _jsxs(CardContent, { className: "flex flex-col gap-1 text-sm", "data-testid": "training-note", children: [_jsxs("p", { children: [_jsx("strong", { children: "About this signal." }), " TIHM has no falls, so this is a SisFall recording (scripted falls by volunteers on mats) injected into a real patient-day. ", d.injected ? "It was injected from the UI." : "It came from the scheduled replay."] }), _jsxs("p", { children: ["SisFall subject ", _jsx("span", { className: "num font-semibold", children: d.sisfall_subject ?? "n/a" }), ". In the training set of the Fall worker's model: ", _jsx("strong", { children: yn(d.in_training_set_of_worker_model) }), ". In the training set of the final model (fall_v2): ", _jsx("strong", { children: yn(d.in_training_set_of_final_model) }), "."] })] }) })] }));
}
export default function FallMonitor() {
    const [sp, setSp] = useSearchParams();
    const patients = usePatients();
    const falls = useFalls();
    const sim = useSimFall();
    const pid = sp.get("patient") ?? "";
    const fid = sp.get("fall") ? Number(sp.get("fall")) : undefined;
    const [simPid, setSimPid] = useState("");
    const [waiting, setWaiting] = useState(null);
    const [note, setNote] = useState("");
    const ids = patients.data?.items.map((p) => p.patient_id) ?? [];
    const mine = (falls.data?.items ?? []).filter((f) => !pid || f.patient_id === pid);
    const detail = useFall(fid, waiting === fid);
    useEffect(() => { if (!simPid && ids[0])
        setSimPid(ids[0]); }, [ids, simPid]);
    useEffect(() => { if (fid === undefined && mine[0] && waiting === null)
        setSp((p) => { p.set("fall", String(mine[0].id)); return p; }, { replace: true }); }, [fid, mine, waiting, setSp]);
    useEffect(() => {
        if (waiting === null)
            return;
        if (detail.data) {
            setWaiting(null);
            setNote("");
        }
        const t = setTimeout(() => { if (waiting !== null && !detail.data) {
            setWaiting(null);
            setNote("The Fall agent did not flag this window (its probability stayed below the threshold), so no alert was raised. Try again: some SisFall fall types are missed.");
        } }, 25000);
        return () => clearTimeout(t);
    }, [waiting, detail.data]);
    const simulate = () => sim.mutate({ patient_id: simPid }, {
        onSuccess: (r) => { setNote(""); if (r.event_id != null) {
            setWaiting(r.event_id);
            setSp({ patient: r.patient_id, fall: String(r.event_id) });
        }
        else
            setNote(r.note); },
        onError: (e) => setNote(e.message),
    });
    return (_jsxs("div", { className: "flex flex-col gap-4", children: [_jsxs("div", { className: "flex flex-wrap items-end justify-between gap-3", children: [_jsx("h1", { className: "text-2xl font-semibold", children: "Fall Monitor" }), _jsxs("div", { className: "flex flex-wrap items-center gap-2", children: [_jsxs(Select, { value: simPid, onValueChange: setSimPid, children: [_jsx(SelectTrigger, { className: "w-36", "aria-label": "Patient to simulate a fall for", children: _jsx(SelectValue, { placeholder: "Patient" }) }), _jsx(SelectContent, { children: ids.map((i) => _jsx(SelectItem, { value: i, children: i }, i)) })] }), _jsx(Button, { onClick: simulate, disabled: !simPid || sim.isPending, "data-testid": "simulate-fall", children: sim.isPending ? "Injecting…" : "Simulate fall" })] })] }), note && _jsx("p", { role: "status", className: "rounded-md border border-monitor-line bg-monitor-bg px-3 py-2 text-sm text-monitor-fg", "data-testid": "sim-note", children: note }), waiting !== null && _jsx("p", { role: "status", className: "text-sm text-muted-foreground", children: "Waiting for the Fall agent to score the new window\u2026" }), _jsxs("div", { className: "grid gap-4 lg:grid-cols-[16rem_1fr]", children: [_jsxs("div", { className: "flex flex-col gap-2", children: [_jsxs(Select, { value: pid || "all", onValueChange: (v) => setSp(v === "all" ? {} : { patient: v }), children: [_jsx(SelectTrigger, { "aria-label": "Patient filter", children: _jsx(SelectValue, {}) }), _jsxs(SelectContent, { children: [_jsx(SelectItem, { value: "all", children: "All patients" }), ids.map((i) => _jsx(SelectItem, { value: i, children: i }, i))] })] }), falls.isLoading ? _jsx(Skeleton, { className: "h-40 w-full" }) : falls.isError ? _jsx(ErrorNote, { error: falls.error })
                                : mine.length === 0 ? _jsx(Empty, { title: "No falls yet", children: "Press Simulate fall to inject one." }) : (_jsx("ul", { className: "flex max-h-[32rem] flex-col gap-1.5 overflow-y-auto", "aria-label": "Falls", tabIndex: 0, "data-testid": "fall-list", children: mine.map((f) => (_jsx("li", { children: _jsxs("button", { onClick: () => setSp({ ...(pid ? { patient: pid } : {}), fall: String(f.id) }), "aria-current": f.id === fid, className: `w-full rounded-lg border p-2.5 text-left text-sm transition-colors hover:bg-secondary ${f.id === fid ? "border-primary bg-accent" : "bg-card"}`, children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsx("span", { className: "num font-semibold", children: f.patient_id }), _jsx("span", { className: "num text-xs text-muted-foreground", children: fmtTime(f.ts) })] }), _jsxs("div", { className: "mt-1 flex items-center justify-between gap-2", children: [_jsxs("span", { children: ["probability ", _jsx("span", { className: "num", children: f.probability_text })] }), _jsx(StatusBadge, { status: f.day_status, className: "text-xs" })] })] }) }, f.id))) }))] }), _jsx("div", { children: fid === undefined ? _jsx(Empty, { title: "Select a fall", children: "Pick a fall from the list to see its waveform and the alert it produced." })
                            : detail.isLoading || (detail.isError && waiting === fid) ? _jsx(Skeleton, { className: "h-96 w-full" })
                                : detail.isError ? _jsx(ErrorNote, { error: detail.error }) : _jsx(Detail, { d: detail.data }) })] })] }));
}
