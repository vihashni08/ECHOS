import { useState } from "react"
import { CheckCircle2, Clock, Send, XCircle } from "lucide-react"
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { useConfig, useOutbox, useSendWeekly } from "@/lib/queries"
import type { Outbox } from "@/lib/api"

export function DeliveryStatus({ o }: { o: Pick<Outbox, "status" | "delivery"> }) {
  const I = o.status === "SENT" ? CheckCircle2 : o.status === "FAILED" ? XCircle : Clock
  return <span className="inline-flex items-center gap-1 font-semibold"><I className="size-4" aria-hidden="true" />{o.status}</span>
}

/** 'Send to clinician': confirmation that states dry run or real email; a second confirmation to resend a week that was already sent. */
export default function SendWeekly({ pid, week, label = "Send to clinician", variant = "default" }: { pid: string; week: string; label?: string; variant?: "default" | "outline" }) {
  const cfg = useConfig()
  const outbox = useOutbox(100)
  const send = useSendWeekly()
  const [step, setStep] = useState<"confirm" | "again" | null>(null)
  const dry = cfg.data?.dry_run_mail ?? true
  const already = (outbox.data?.items ?? []).some((o) => o.kind === "weekly" && o.alert_id.startsWith(`weekly|${pid}|${week}`))
  const last = send.data?.outbox[0]
  const go = (force: boolean) => { setStep(null); send.mutate({ pid, week, force }) }
  return (
    <div className="flex flex-col gap-2">
      <Button variant={variant} onClick={() => setStep("confirm")} disabled={send.isPending || !pid || !week} data-testid="send-weekly"><Send className="size-4" aria-hidden="true" />{send.isPending ? "Sending…" : label}</Button>
      {send.isError && <p role="alert" className="text-sm text-destructive">{(send.error as Error).message}</p>}
      {last && !send.isPending && (
        <p role="status" className="text-sm" data-testid="send-result">
          {send.data!.deduplicated ? "Already sent this week; nothing new was queued. " : ""}Outbox: <DeliveryStatus o={last} /> · {last.delivery === "dry_run" ? "dry run (nothing was emailed)" : "real email"} to <span className="num">{last.address}</span> · <span className="num">{last.attachment_name}</span>
        </p>
      )}
      <AlertDialog open={step === "confirm"} onOpenChange={(o) => !o && setStep((s) => (s === "confirm" ? null : s))}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Send the weekly report to the clinician?</AlertDialogTitle>
            <AlertDialogDescription>
              Patient {pid}, week {week}. Mode: <strong>{dry ? "DRY RUN: nothing is emailed; only an outbox row is created" : "REAL EMAIL: the PDF is emailed to the clinician address"}</strong>.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => (already ? setStep("again") : go(false))} data-testid="confirm-send">{dry ? "Send (dry run)" : "Send email"}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <AlertDialog open={step === "again"} onOpenChange={(o) => !o && setStep((s) => (s === "again" ? null : s))}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>This week was already sent</AlertDialogTitle>
            <AlertDialogDescription>The report for {pid}, {week} is already in the outbox. Send another copy ({dry ? "dry run" : "real email"})?</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep one copy</AlertDialogCancel>
            <AlertDialogAction onClick={() => go(true)} data-testid="confirm-resend">Send another copy</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
