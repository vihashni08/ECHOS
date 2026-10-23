import { Link } from "react-router-dom"
import { AlertCard, isOpen } from "@/components/AlertCard"
import EvidenceCard from "@/components/EvidenceCard"
import ReviewList from "@/components/ReviewList"
import { CardSkeleton, Empty, ErrorNote } from "@/components/States"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { personLabel } from "@/lib/live"
import { useAlertList, useEvidence, usePatients, useReview, useSummary } from "@/lib/queries"
import { StatusBadge } from "@/lib/status"

function Stat({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="rounded-xl border bg-card p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="num text-2xl font-semibold">{value}</div>
      {sub && <div className="text-xs text-muted-foreground">{sub}</div>}
    </div>
  )
}

const CLAIMS = [
  { n: "1", t: "Audit first", d: "We checked what the labels are before modelling. 88% of days at or above 160/100 are labelled, so detecting them re-learns a threshold. Thresholds stay rules." },
  { n: "2", t: "Balanced, personal ranking", d: "Personal Bayesian baselines put BP and agitation on one scale (lift over each base rate), under a daily budget with a fairness floor." },
  { n: "3", t: "Evidence you can check", d: "Every urgent alert and review card cites a guideline passage. Gemini writes the wording; a validator rejects anything not traceable to the facts or the passage." },
]

export default function Today() {
  const r = useReview()
  const s = useSummary()
  const a = useAlertList(50)
  const p = usePatients()
  const ev = useEvidence(r.data?.day ?? undefined)
  const evUrgent = useEvidence()
  const open = (a.data?.items ?? []).filter(isOpen)
  const sum = s.data
  const cutoff = r.data?.items.filter((i) => i.reason !== "fairness").reduce((m, i) => Math.min(m, i.priority), Infinity)
  return (
    <div className="flex flex-col gap-5">
      <div>
        <h1 className="text-2xl font-semibold">Morning briefing</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">Who the clinical team should look at today, and why. The Review Coordinator picks {r.data?.k ?? 4} people a day from every Patient Monitor's request; red-flag readings never wait for this list.</p>
      </div>
      <section aria-label="What is new" className="grid gap-3 md:grid-cols-3" data-testid="claims">
        {CLAIMS.map((c) => (
          <div key={c.n} className="rounded-xl border bg-card p-3 text-sm"><div className="mb-1 flex items-center gap-2 font-semibold"><span className="num flex size-6 items-center justify-center rounded-full border text-xs">{c.n}</span>{c.t}</div><p className="text-muted-foreground">{c.d}</p></div>))}
        <p className="text-xs text-muted-foreground md:col-span-3">Not new, and said plainly: Bayesian updating, XGBoost, Platt scaling, TF-IDF retrieval and the NEWS2 / NICE rules are established. The contribution is the audit, the balanced policy and the verified evidence, evaluated by patient. <Link to="/test" className="underline">Test it yourself</Link>.</p>
      </section>
      <section aria-label="Summary" className="grid grid-cols-2 gap-3 md:grid-cols-4" data-testid="stats">
        <Stat label="Patient-days scored" value={sum?.patient_days_scored ?? "–"} />
        <Stat label="Urgent alerts" value={sum?.urgent_alerts ?? "–"} sub="red-flag readings" />
        <Stat label="Reviews assigned" value={sum?.reviews ?? "–"} sub={sum ? `${sum.reviews_confirmed} found a confirmed event` : undefined} />
        <Stat label="Evidence cards" value={sum ? Object.values((sum as unknown as { evidence_cards?: Record<string, number> }).evidence_cards ?? {}).reduce((x, y) => x + y, 0) : "–"} sub="cited explanations" />
      </section>
      {open.length > 0 && (
        <section aria-label="Urgent alerts" className="flex flex-col gap-2" data-testid="urgent-pinned">
          <h2 className="text-lg font-semibold">Urgent, outside the budget</h2>
          {open.slice(0, 3).map((x) => (
            <div key={x.alert_id} className="flex flex-col gap-2"><AlertCard a={x} compact /><EvidenceCard card={evUrgent.data?.find((c) => c.kind === "urgent" && c.card_id.endsWith(x.alert_id))} /></div>))}
          {open.length > 3 && <Link to="/alerts" className="text-sm underline">{open.length - 3} more on the Alerts page</Link>}
        </section>)}
      <div className="grid gap-5 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <Card>
          <CardHeader><CardTitle>Review list {r.data?.day ? <span className="num font-normal text-muted-foreground">· {r.data.day}</span> : null}</CardTitle></CardHeader>
          <CardContent>
            {r.isError ? <ErrorNote error={r.error} /> : !r.data ? <CardSkeleton /> : r.data.items.length === 0
              ? <Empty title="No review list yet">Press Play in Demo tools: the first list appears when the first replayed day closes.</Empty>
              : <ReviewList items={r.data.items} evidence={ev.data ?? []} cutoff={Number.isFinite(cutoff) ? cutoff : undefined} />}
            {r.data?.day && <p className="mt-3 text-sm"><Link to={`/review?day=${r.data.day}`} className="underline">Open the review board</Link> to record outcomes or see earlier days.</p>}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Caseload</CardTitle></CardHeader>
          <CardContent>
            {!p.data ? <CardSkeleton h="h-24" /> : (
              <table className="w-full text-sm" data-testid="caseload">
                <thead><tr className="text-left text-xs text-muted-foreground"><th className="py-1 font-medium">Person</th><th className="font-medium">Latest day</th><th className="font-medium">Status</th><th className="text-right font-medium">Reviews</th></tr></thead>
                <tbody>{p.data.items.map((x) => (
                  <tr key={x.patient_id} className="border-t">
                    <td className="py-1.5"><Link to={`/patients/${x.patient_id}`} className="num underline">{personLabel(x.patient_id)}</Link><div className="text-xs text-muted-foreground">{x.age_band}</div></td>
                    <td className="num">{x.latest_day ?? "–"}</td>
                    <td><StatusBadge status={x.latest_status} className="text-xs" /></td>
                    <td className="num text-right">{x.reviews}</td>
                  </tr>))}</tbody>
              </table>)}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
