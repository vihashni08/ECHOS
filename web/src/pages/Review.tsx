import { ChevronLeft, ChevronRight } from "lucide-react"
import { Link, useSearchParams } from "react-router-dom"
import ReviewList from "@/components/ReviewList"
import { CardSkeleton, Empty, ErrorNote } from "@/components/States"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { personLabel } from "@/lib/live"
import { useReview } from "@/lib/queries"

export default function Review() {
  const [sp, setSp] = useSearchParams()
  const day = sp.get("day") || undefined
  const r = useReview(day)
  const days = r.data?.days ?? []              // newest first
  const i = r.data?.day ? days.indexOf(r.data.day) : -1
  const go = (d?: string) => d && setSp({ day: d })
  return (
    <div className="flex flex-col gap-5">
      <div>
        <h1 className="text-2xl font-semibold">Review board</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">The Review Coordinator's decision for each day: the {r.data?.k ?? 4} people the clinical team should look at, ranked by how far each person's risk is above its base rate, plus anyone not reviewed for a week (fairness). The clinician's answer goes back to that person's Patient Monitor, which learns from it. In the replay the TIHM study record fills in the outcome automatically.</p>
      </div>
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Choose a day">
        <Button variant="outline" size="sm" onClick={() => go(days[i + 1])} disabled={i < 0 || i >= days.length - 1} aria-label="Previous day"><ChevronLeft className="size-4" aria-hidden="true" />Earlier</Button>
        <span className="num min-w-28 text-center font-semibold" data-testid="review-day">{r.data?.day ?? "no day yet"}</span>
        <Button variant="outline" size="sm" onClick={() => go(days[i - 1])} disabled={i <= 0} aria-label="Next day">Later<ChevronRight className="size-4" aria-hidden="true" /></Button>
        {day && <Button variant="ghost" size="sm" onClick={() => setSp({})}>Latest</Button>}
        <span className="text-sm text-muted-foreground">{days.length} day(s) allocated</span>
      </div>
      {r.isError && <ErrorNote error={r.error} />}
      <div className="grid gap-5 lg:grid-cols-[minmax(0,3fr)_minmax(0,1fr)]">
        <Card><CardHeader><CardTitle>Selected for review</CardTitle></CardHeader><CardContent>
          {!r.data ? <CardSkeleton /> : r.data.items.length === 0 ? <Empty title="No review list yet">The first list appears when the first replayed day closes.</Empty> : <ReviewList items={r.data.items} withActions />}
        </CardContent></Card>
        <Card><CardHeader><CardTitle>Not selected</CardTitle></CardHeader><CardContent>
          {!r.data?.deferred.length ? <p className="text-sm text-muted-foreground">Nobody else asked for a review this day.</p> : (
            <ul className="flex flex-col gap-1 text-sm" data-testid="deferred">{r.data.deferred.map((p) => <li key={p}><Link className="num underline" to={`/patients/${p}`}>{personLabel(p)}</Link></li>)}</ul>)}
          <p className="mt-3 text-xs text-muted-foreground">They stay on the dashboard as Watch (if their risk is at least 1.5× a base rate) or Normal, and come back as soon as their priority rises.</p>
        </CardContent></Card>
      </div>
    </div>
  )
}
