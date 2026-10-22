import { Link } from "react-router-dom"
import { AlertCard, isOpen } from "@/components/AlertCard"
import ReviewList from "@/components/ReviewList"
import { CardSkeleton, Empty, ErrorNote } from "@/components/States"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { personLabel } from "@/lib/live"
import { useAlertList, usePatients, useReview, useSummary } from "@/lib/queries"
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

export default function Today() {
  const r = useReview()
  const s = useSummary()
  const a = useAlertList(50)
  const p = usePatients()
  const open = (a.data?.items ?? []).filter(isOpen)
  const sum = s.data
  return (
    <div className="flex flex-col gap-5">
      <div>
        <h1 className="text-2xl font-semibold">Today</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">Who the clinical team should look at today, and why. The Review Coordinator picks {r.data?.k ?? 4} people a day from every Patient Monitor's request; red-flag readings never wait for this list (see Alerts).</p>
      </div>
      <section aria-label="Summary" className="grid grid-cols-2 gap-3 md:grid-cols-4" data-testid="stats">
        <Stat label="Patient-days scored" value={sum?.patient_days_scored ?? "–"} />
        <Stat label="Urgent alerts" value={sum?.urgent_alerts ?? "–"} sub="red-flag readings" />
        <Stat label="Reviews assigned" value={sum?.reviews ?? "–"} sub={sum ? `${sum.reviews_confirmed} found a confirmed event` : undefined} />
        <Stat label="Agent messages" value={sum ? Object.values(sum.messages).reduce((x, y) => x + y, 0) : "–"} sub="on the bus" />
      </section>
      <div className="grid gap-5 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <Card>
          <CardHeader><CardTitle>Review list {r.data?.day ? <span className="num font-normal text-muted-foreground">· {r.data.day}</span> : null}</CardTitle></CardHeader>
          <CardContent>
            {r.isError ? <ErrorNote error={r.error} /> : !r.data ? <CardSkeleton /> : r.data.items.length === 0
              ? <Empty title="No review list yet">Press Play in Demo tools: the first list appears when the first replayed day closes.</Empty>
              : <ReviewList items={r.data.items} />}
            {r.data?.day && <p className="mt-3 text-sm"><Link to={`/review?day=${r.data.day}`} className="underline">Open the review board</Link> to record outcomes or see earlier days.</p>}
          </CardContent>
        </Card>
        <div className="flex flex-col gap-5">
          <Card>
            <CardHeader><CardTitle>Urgent alerts waiting</CardTitle></CardHeader>
            <CardContent className="flex flex-col gap-2">
              {open.length === 0 ? <p className="text-sm text-muted-foreground">None waiting for acknowledgement.</p> : open.slice(0, 3).map((x) => <AlertCard key={x.alert_id} a={x} compact />)}
              {open.length > 3 && <Link to="/alerts" className="text-sm underline">{open.length - 3} more on the Alerts page</Link>}
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
    </div>
  )
}
