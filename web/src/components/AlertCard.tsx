import { Check, ChevronRight, ShieldCheck, Siren, TimerReset } from "lucide-react"
import { Link } from "react-router-dom"
import { Button } from "@/components/ui/button"
import { fmtTime, typeLabel } from "@/lib/format"
import { mmss, personLabel, useNow } from "@/lib/live"
import { useAck, useResolve } from "@/lib/queries"
import type { AlertRow } from "@/lib/api"
import { cn } from "@/lib/utils"

export const STATE_TEXT: Record<string, string> = {
  NEW: "Being delivered", SENT: "Delivered: waiting for someone to acknowledge", ACKNOWLEDGED: "Acknowledged", RESOLVED: "Resolved", ESCALATED: "Escalated: not acknowledged in time", EXPIRED: "Expired: too old to act on (history)",
}
// EXPIRED = too old to act on (history after a restart or a replay): shown as history, never as "needs action now"
export const isOpen = (a: AlertRow) => a.state !== "ACKNOWLEDGED" && a.state !== "RESOLVED" && a.state !== "EXPIRED"

export function AlertCard({ a, compact }: { a: AlertRow; compact?: boolean }) {
  const now = useNow()
  const ack = useAck()
  const res = useResolve()
  const left = a.deadline_ts ? a.deadline_ts - now : null
  const st = a.state ?? "NEW"
  const open = isOpen(a)
  return (
    <article data-testid="alert-card" data-state={st} aria-label={`${typeLabel(a.type)} for patient ${a.patient_id}`}
      className={cn("flex flex-col gap-2 rounded-xl border p-3 text-sm", open ? "border-urgent-line bg-urgent-bg text-urgent-fg" : "bg-card")}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex items-start gap-2">
          <Siren className="mt-0.5 size-5 shrink-0" aria-hidden="true" />
          <div>
            <div className="text-base font-semibold">{typeLabel(a.type)} · <Link to={`/patients/${a.patient_id}`} className="num underline">{personLabel(a.patient_id)}</Link></div>
            <div className="num opacity-80">{fmtTime(a.event_time)} (data time)</div>
          </div>
        </div>
        <span className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-semibold" data-testid="alert-state">
          {st === "ACKNOWLEDGED" || st === "RESOLVED" ? <ShieldCheck className="size-3.5" aria-hidden="true" /> : <TimerReset className="size-3.5" aria-hidden="true" />}{st}
        </span>
      </div>
      <p>{STATE_TEXT[st] ?? st}{st === "SENT" && left != null && <> · escalates to the second contact in <span className="num font-semibold" data-testid="countdown">{mmss(left)}</span></>}
        {st === "ACKNOWLEDGED" && a.ack_by && <> by {a.ack_by}</>}{a.note ? <> · {a.note}</> : null}</p>
      {!compact && a.status === "FAILED" && <p className="font-medium">E-mail delivery failed: check the Alerts page.</p>}
      <div className="flex flex-wrap gap-2">
        {open && <Button size="sm" onClick={() => ack.mutate({ id: a.alert_id })} disabled={ack.isPending} data-testid="ack"><Check className="size-4" aria-hidden="true" />I have seen this: acknowledge</Button>}
        {st === "ACKNOWLEDGED" && <Button size="sm" variant="outline" onClick={() => res.mutate({ id: a.alert_id, note: "handled" })} disabled={res.isPending} data-testid="resolve">Mark as handled</Button>}
        <Button size="sm" variant="outline" asChild><Link to={`/patients/${a.patient_id}`}>Open case<ChevronRight className="size-4" aria-hidden="true" /></Link></Button>
      </div>
    </article>
  )
}
