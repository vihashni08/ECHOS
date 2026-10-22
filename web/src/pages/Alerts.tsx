import { useState } from "react"
import { Link } from "react-router-dom"
import { AlertCard, isOpen } from "@/components/AlertCard"
import { Empty, ErrorNote } from "@/components/States"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { useAlertList, useOutbox, useTasks } from "@/lib/queries"
import { channelLabel, fmtEpoch, fmtTime } from "@/lib/format"
import { personLabel } from "@/lib/live"

const ROLE: Record<string, string> = { caregiver: "Caregiver", clinician: "Clinician", escalation: "Second contact (escalation)" }

export default function Alerts() {
  const a = useAlertList(100)
  const o = useOutbox(30)
  const t = useTasks(undefined)
  const [tab, setTab] = useState<"open" | "all">("open")
  const rows = (a.data?.items ?? []).filter((x) => tab === "all" || isOpen(x))
  return (
    <div className="flex flex-col gap-5">
      <div><h1 className="text-2xl font-semibold">Alerts</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">An URGENT alert (a red-flag reading: NEWS2 red score or BP at or above 180/120) is sent at once to the caregiver and the clinician by the Care agent. It then waits for someone to acknowledge it; if no one does in time, the same alert goes to a second contact. Nothing here is a diagnosis.</p></div>
      <div role="tablist" aria-label="Alert filter" className="flex gap-2">
        {(["open", "all"] as const).map((k) => <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)} data-testid={`tab-${k}`}
          className={`rounded-md border px-3 py-1.5 text-sm font-medium ${tab === k ? "bg-accent text-accent-foreground" : "bg-card"}`}>{k === "open" ? "Waiting for action" : "All alerts"}</button>)}
      </div>
      {a.isError && <ErrorNote error={a.error} />}
      {rows.length === 0 ? <Empty title={tab === "open" ? "No alert is waiting" : "No alerts yet"}>Red-flag alerts appear here the moment the Care agent queues them.</Empty>
        : <div className="flex flex-col gap-2" data-testid="alert-list">{rows.map((x) => <AlertCard key={x.alert_id} a={x} />)}</div>}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card><CardHeader><CardTitle>Care tasks (review assignments)</CardTitle></CardHeader><CardContent>
          {!t.data?.items.length ? <p className="text-sm text-muted-foreground">No tasks yet.</p> : (
            <ul className="flex flex-col gap-2 text-sm">{t.data.items.map((x) => <li key={x.id} className="flex flex-wrap justify-between gap-2 rounded-lg border p-2.5">
              <span><span className="font-medium">{x.title}</span> · <Link className="underline" to={`/patients/${x.patient_id}`}>{personLabel(x.patient_id)}</Link></span><span className="font-semibold">{x.status}</span></li>)}</ul>)}
        </CardContent></Card>
        <Card><CardHeader><CardTitle>Delivery log</CardTitle></CardHeader><CardContent>
          {!o.data?.items.length ? <p className="text-sm text-muted-foreground">Nothing sent yet.</p> : (
            <ul className="flex flex-col gap-2 text-sm" data-testid="delivery-log">{o.data.items.slice(0, 12).map((x) => <li key={x.outbox_id} className="rounded-lg border p-2.5">
              <div className="font-medium">{ROLE[x.recipient_role] ?? x.recipient_role} · {x.status}</div>
              <div className="num text-muted-foreground">{x.address} · {channelLabel(x.channel)} · {x.sent_time ? fmtEpoch(x.sent_time) : fmtTime(x.event_time)}</div></li>)}</ul>)}
        </CardContent></Card>
      </div>
    </div>
  )
}
