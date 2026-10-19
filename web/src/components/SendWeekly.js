import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState } from "react";
import { CheckCircle2, Clock, Send, XCircle } from "lucide-react";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { useConfig, useOutbox, useSendWeekly } from "@/lib/queries";
export function DeliveryStatus({ o }) {
    const I = o.status === "SENT" ? CheckCircle2 : o.status === "FAILED" ? XCircle : Clock;
    return _jsxs("span", { className: "inline-flex items-center gap-1 font-semibold", children: [_jsx(I, { className: "size-4", "aria-hidden": "true" }), o.status] });
}
/** 'Send to clinician': confirmation that states dry run or real email; a second confirmation to resend a week that was already sent. */
export default function SendWeekly({ pid, week, label = "Send to clinician", variant = "default" }) {
    const cfg = useConfig();
    const outbox = useOutbox(100);
    const send = useSendWeekly();
    const [step, setStep] = useState(null);
    const dry = cfg.data?.dry_run_mail ?? true;
    const already = (outbox.data?.items ?? []).some((o) => o.kind === "weekly" && o.alert_id.startsWith(`weekly|${pid}|${week}`));
    const last = send.data?.outbox[0];
    const go = (force) => { setStep(null); send.mutate({ pid, week, force }); };
    return (_jsxs("div", { className: "flex flex-col gap-2", children: [_jsxs(Button, { variant: variant, onClick: () => setStep("confirm"), disabled: send.isPending || !pid || !week, "data-testid": "send-weekly", children: [_jsx(Send, { className: "size-4", "aria-hidden": "true" }), send.isPending ? "Sending…" : label] }), send.isError && _jsx("p", { role: "alert", className: "text-sm text-destructive", children: send.error.message }), last && !send.isPending && (_jsxs("p", { role: "status", className: "text-sm", "data-testid": "send-result", children: [send.data.deduplicated ? "Already sent this week; nothing new was queued. " : "", "Outbox: ", _jsx(DeliveryStatus, { o: last }), " \u00B7 ", last.delivery === "dry_run" ? "dry run (nothing was emailed)" : "real email", " to ", _jsx("span", { className: "num", children: last.address }), " \u00B7 ", _jsx("span", { className: "num", children: last.attachment_name })] })), _jsx(AlertDialog, { open: step === "confirm", onOpenChange: (o) => !o && setStep((s) => (s === "confirm" ? null : s)), children: _jsxs(AlertDialogContent, { children: [_jsxs(AlertDialogHeader, { children: [_jsx(AlertDialogTitle, { children: "Send the weekly report to the clinician?" }), _jsxs(AlertDialogDescription, { children: ["Patient ", pid, ", week ", week, ". Mode: ", _jsx("strong", { children: dry ? "DRY RUN: nothing is emailed; only an outbox row is created" : "REAL EMAIL: the PDF is emailed to the clinician address" }), "."] })] }), _jsxs(AlertDialogFooter, { children: [_jsx(AlertDialogCancel, { children: "Cancel" }), _jsx(AlertDialogAction, { onClick: () => (already ? setStep("again") : go(false)), "data-testid": "confirm-send", children: dry ? "Send (dry run)" : "Send email" })] })] }) }), _jsx(AlertDialog, { open: step === "again", onOpenChange: (o) => !o && setStep((s) => (s === "again" ? null : s)), children: _jsxs(AlertDialogContent, { children: [_jsxs(AlertDialogHeader, { children: [_jsx(AlertDialogTitle, { children: "This week was already sent" }), _jsxs(AlertDialogDescription, { children: ["The report for ", pid, ", ", week, " is already in the outbox. Send another copy (", dry ? "dry run" : "real email", ")?"] })] }), _jsxs(AlertDialogFooter, { children: [_jsx(AlertDialogCancel, { children: "Keep one copy" }), _jsx(AlertDialogAction, { onClick: () => go(true), "data-testid": "confirm-resend", children: "Send another copy" })] })] }) })] }));
}
