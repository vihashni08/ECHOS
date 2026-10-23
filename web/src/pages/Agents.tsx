import { useState } from "react"
import { Link } from "react-router-dom"
import AgentStrip, { NAME } from "@/components/AgentStrip"
import { CrashDiff, Risks } from "@/components/CrashAndRisks"
import { CardSkeleton } from "@/components/States"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { personLabel } from "@/lib/live"
import { useComponents, useMessages } from "@/lib/queries"

const TOPIC: Record<string, string> = { urgent: "URGENT reading", review_request: "review request", feedback: "feedback (outcome)", assign: "assign review",
  review_decision: "review decision", evidence_request: "evidence request", evidence_ready: "evidence ready", urgent_sent: "alert queued", urgent_duplicate: "duplicate alert ignored", alert_escalated: "alert escalated", clinician_outcome: "clinician outcome" }
const who = (x: string) => NAME[x] ?? (x === "all" ? "everyone (log)" : x === "clinician" ? "Clinician (API)" : x)

function summarise(topic: string, p: Record<string, unknown>): string {
  const f = (v: unknown, d = 0) => (typeof v === "number" ? `${(v * 100).toFixed(d)}%` : "n/a")
  if (topic === "review_request") return `vitals risk ${f(p.vitals_risk)}, agitation ${f(p.agitation_risk, 1)}`
  if (topic === "assign") return `rank ${p.rank} (${p.reason}), priority ${p.priority}`
  if (topic === "feedback" || topic === "clinician_outcome") return `BP/pulse event: ${p.vitals_event ? "yes" : "no"}; agitation windows: ${(p.agitation_windows as unknown[] | undefined)?.length ?? 0}`
  if (topic === "urgent") return ((p.reasons as string[] | undefined) ?? []).join("; ")
  if (topic === "review_decision") return `selected ${(p.selected as unknown[] | undefined)?.length ?? 0}, deferred ${(p.deferred as unknown[] | undefined)?.length ?? 0}`
  return ""
}

export default function Agents() {
  const comps = useComponents()
  const [topic, setTopic] = useState("")
  const msgs = useMessages(120)
  const rows = (msgs.data ?? []).filter((m) => !topic || m.topic === topic)
  const topics = [...new Set((msgs.data ?? []).map((m) => m.topic))]
  return (
    <div className="flex flex-col gap-5">
      <div>
        <h1 className="text-2xl font-semibold">Under the hood</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">Four agents, each its own process with its own goal, memory and decisions: three decide, one cites the evidence. They never call each other: every hand-over is a message on the bus below, so you can follow who told whom what.</p>
      </div>
      <AgentStrip />
      <section aria-label="The agents" className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {!comps.data ? <CardSkeleton /> : comps.data.filter((c) => c.kind === "agent").map((c) => (
          <Card key={c.id}><CardHeader><CardTitle>{c.name}</CardTitle></CardHeader><CardContent className="flex flex-col gap-1.5 text-sm">
            <p>{c.role}</p>
            <p><span className="font-medium">Receives:</span> {c.inputs}</p>
            <p><span className="font-medium">Sends:</span> {c.outputs}</p>
            <p><span className="font-medium">Never:</span> {c.never}</p>
            {c.detail && <p className="num text-muted-foreground">{c.detail}</p>}
          </CardContent></Card>))}
      </section>
      <CrashDiff />
      <Card>
        <CardHeader><CardTitle>Message trace (newest first)</CardTitle></CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex flex-wrap gap-2" role="group" aria-label="Filter by topic">
            {["", ...topics].map((t) => <button key={t || "all"} onClick={() => setTopic(t)} aria-pressed={topic === t}
              className={`rounded-md border px-2.5 py-1 text-xs font-medium ${topic === t ? "bg-accent text-accent-foreground" : "bg-card"}`}>{t ? TOPIC[t] ?? t : "all topics"}</button>)}
          </div>
          {!rows.length ? <p className="text-sm text-muted-foreground">No messages yet: press Play in Demo tools.</p> : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm" data-testid="message-trace">
                <thead><tr className="text-left text-xs text-muted-foreground"><th className="py-1 font-medium">From</th><th className="font-medium">To</th><th className="font-medium">Message</th><th className="font-medium">Person / day</th><th className="font-medium">Content</th></tr></thead>
                <tbody>{rows.map((m) => (
                  <tr key={m.id} className="border-t align-top">
                    <td className="py-1.5 pr-2 whitespace-nowrap">{who(m.sender)}</td><td className="pr-2 whitespace-nowrap">{who(m.recipient)}</td>
                    <td className="pr-2 font-medium whitespace-nowrap">{TOPIC[m.topic] ?? m.topic}</td>
                    <td className="num pr-2 whitespace-nowrap">{m.patient_id ? <Link className="underline" to={`/patients/${m.patient_id}`}>{personLabel(m.patient_id)}</Link> : "–"} {m.day}</td>
                    <td className="text-muted-foreground">{summarise(m.topic, m.payload as Record<string, unknown>)}</td>
                  </tr>))}</tbody>
              </table>
            </div>)}
        </CardContent>
      </Card>
      <Risks />
    </div>
  )
}
