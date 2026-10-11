import { Siren } from "lucide-react"
import { Link } from "react-router-dom"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Empty } from "./States"
import { useAlerts } from "@/lib/queries"
import { useEvents } from "@/lib/events"
import { fmtTime, typeLabel } from "@/lib/format"

export default function AlertFeed() {
  const { feed, conn } = useEvents()
  const hist = useAlerts(20)
  const seen = new Set(feed.map((f) => f.alert_id))
  const rows = [
    ...feed.map((f) => ({ id: f.alert_id, pid: f.patient_id, type: f.type, t: f.event_time, live: true })),
    ...(hist.data?.items ?? []).filter((a) => !seen.has(a.alert_id)).map((a) => ({ id: a.alert_id, pid: a.patient_id, type: a.type, t: a.event_time ?? null, live: false })),
  ].sort((a, b) => (b.t ?? "").localeCompare(a.t ?? ""))
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Alert feed</CardTitle>
        <span className="text-xs text-muted-foreground" data-testid="feed-conn">{conn === "open" ? "live" : "offline, retrying"}</span>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? <Empty title="No alerts yet">Alerts appear here the moment they are raised.</Empty> : (
          <ol className="flex max-h-96 flex-col gap-2 overflow-y-auto" role="log" aria-live="polite" aria-label="Alerts, newest first" data-testid="alert-feed">
            {rows.map((r) => (
              <li key={r.id} data-testid="alert-item" className="flex items-start gap-2.5 rounded-lg border border-urgent-line bg-urgent-bg px-3 py-2 text-urgent-fg">
                <Siren className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                <div className="min-w-0 text-sm">
                  <div className="font-semibold">URGENT · {typeLabel(r.type)}{r.live && <span className="ml-2 rounded bg-urgent-fg px-1.5 py-0.5 text-[10px] font-bold uppercase text-urgent-bg">new</span>}</div>
                  <div><Link to={`/patients/${r.pid}`} className="num underline">{r.pid}</Link> <span className="num">{fmtTime(r.t)}</span></div>
                </div>
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  )
}
