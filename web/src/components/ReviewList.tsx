import { CheckCircle2, CircleDashed, Scale, XCircle } from "lucide-react"
import { Link } from "react-router-dom"
import { Button } from "@/components/ui/button"
import EvidenceCard from "@/components/EvidenceCard"
import type { Schemas } from "@/lib/api"
import { pct, personLabel } from "@/lib/live"
import { useOutcome, type EvidenceCardT } from "@/lib/queries"
import { cn } from "@/lib/utils"

type Item = Schemas["ReviewItem"]

function Outcome({ o }: { o?: string | null }) {
  if (!o) return <span className="inline-flex items-center gap-1 text-xs text-muted-foreground"><CircleDashed className="size-3.5" aria-hidden="true" />awaiting outcome</span>
  return o === "CONFIRMED"
    ? <span className="inline-flex items-center gap-1 rounded-full border border-normal-line bg-normal-bg px-2 py-0.5 text-xs font-semibold text-normal-fg" data-testid="outcome"><CheckCircle2 className="size-3.5" aria-hidden="true" />event confirmed</span>
    : <span className="inline-flex items-center gap-1 rounded-full border border-gap-line bg-gap-bg px-2 py-0.5 text-xs font-semibold text-gap-fg" data-testid="outcome"><XCircle className="size-3.5" aria-hidden="true" />no event</span>
}

/** Which risk put the person on the list, in words: the larger lift over its own base rate. */
function driver(i: Item) {
  const lv = i.lift_vitals ?? 0, la = i.lift_agitation ?? 0
  return la > lv ? `agitation ${la.toFixed(1)}× usual rate` : `BP / pulse ${lv.toFixed(1)}× usual rate`
}

export default function ReviewList({ items, withActions, evidence, cutoff }: { items: Item[]; withActions?: boolean; evidence?: EvidenceCardT[]; cutoff?: number }) {
  const out = useOutcome()
  return (
    <ol className="flex flex-col gap-2" data-testid="review-list">
      {items.map((i) => (
        <li key={`${i.day}-${i.patient_id}`} className={cn("flex flex-col gap-2 rounded-xl border p-3 text-sm", i.reason === "fairness" ? "bg-card" : "border-monitor-line bg-monitor-bg/40")} data-testid="review-item">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="flex items-center gap-2">
              <span className="num flex size-7 items-center justify-center rounded-full border font-semibold" aria-label={`rank ${i.rank}`}>{i.rank}</span>
              <div>
                <Link to={`/patients/${i.patient_id}`} className="num text-base font-semibold underline">{personLabel(i.patient_id)}</Link>
                <div className="text-xs text-muted-foreground">{i.reason === "fairness" ? "fairness: not reviewed for a week" : driver(i)}</div>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <span className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-semibold" title="Priority = the larger of (vitals risk ÷ its base rate) and (agitation risk ÷ its base rate)">
                <Scale className="size-3.5" aria-hidden="true" />priority {i.priority.toFixed(2)}</span>
              <Outcome o={i.outcome} />
            </div>
          </div>
          <dl className="num grid grid-cols-2 gap-x-4 gap-y-0.5 text-xs sm:max-w-sm">
            <dt className="text-muted-foreground">BP / pulse event, next 3 days</dt><dd className="font-semibold">{pct(i.vitals_risk)}</dd>
            <dt className="text-muted-foreground">Agitation today (highest window)</dt><dd className="font-semibold">{pct(i.agitation_risk, 1)}</dd>
          </dl>
          {i.why.length > 0 && <ul className="list-disc pl-5 text-sm">{i.why.map((w, k) => <li key={k}>{w}</li>)}</ul>}
          {cutoff != null && i.reason !== "fairness" && <p className="num text-xs text-muted-foreground" data-testid="cutoff">priority {i.priority.toFixed(2)} · today's cutoff {cutoff.toFixed(2)} (lowest selected on priority)</p>}
          {evidence && <EvidenceCard card={evidence.find((c) => c.kind === "review" && c.patient_id === i.patient_id && c.day === i.day)} />}
          <Link to={`/trace/${i.patient_id}/${i.day}`} className="self-start text-xs underline" data-testid="open-trace">Open the decision trace</Link>
          {withActions && !i.outcome && (
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="outline" disabled={out.isPending} onClick={() => out.mutate({ patient_id: i.patient_id, day: i.day, vitals_event: true })} data-testid="confirm">Confirm a problem</Button>
              <Button size="sm" variant="outline" disabled={out.isPending} onClick={() => out.mutate({ patient_id: i.patient_id, day: i.day, vitals_event: false })} data-testid="dismiss">No problem found</Button>
            </div>
          )}
        </li>
      ))}
    </ol>
  )
}
