import { ArrowRight, Bot, Cpu, Settings2 } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { CardSkeleton, ErrorNote } from "@/components/States"
import { MODE, useMode } from "@/lib/live"
import { useComponents, useMessages } from "@/lib/queries"
import { personLabel } from "@/lib/live"
import { cn } from "@/lib/utils"
import type { Component } from "@/lib/api"

const KIND = {
  detector: { label: "Detector", icon: Cpu, cls: "bg-gap-bg text-gap-fg border-gap-line", what: "Senses. Turns data into evidence. Never decides a status and never sends anything." },
  agent: { label: "Agent", icon: Bot, cls: "bg-normal-bg text-normal-fg border-normal-line", what: "Has a goal, chooses its next step, uses tools, acts and leaves a trace." },
  infra: { label: "Infrastructure", icon: Settings2, cls: "bg-secondary text-secondary-foreground border-border", what: "Keeps the others running." },
} as const
const STATUS_CLS: Record<string, string> = { running: "text-normal-fg", idle: "text-muted-foreground", missing: "text-urgent-fg", stopped: "text-urgent-fg", "n/a": "text-muted-foreground" }

function Card_({ c }: { c: Component }) {
  const k = KIND[c.kind as keyof typeof KIND] ?? KIND.infra
  const Icon = k.icon
  return (
    <li className="flex flex-col gap-2 rounded-xl border bg-card p-3 text-sm" data-testid={`component-${c.id}`} data-kind={c.kind}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 font-semibold"><Icon className="size-4" aria-hidden="true" />{c.name}</div>
        <span className={cn("rounded-full border px-2 py-0.5 text-xs font-semibold", k.cls)}>{k.label}</span>
      </div>
      <p>{c.role}</p>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-muted-foreground">
        <dt>Takes in</dt><dd className="text-foreground">{c.inputs}</dd><dt>Produces</dt><dd className="text-foreground">{c.outputs}</dd>
        <dt>If it fails</dt><dd className="text-foreground">{c.on_failure}</dd><dt>Never</dt><dd className="text-foreground">{c.never}</dd>
      </dl>
      <div className={cn("font-semibold", STATUS_CLS[c.status])} data-testid="component-status">{c.status}{c.detail ? <span className="font-normal text-muted-foreground"> · {c.detail}</span> : null}{c.heartbeat_age_s != null ? <span className="num font-normal text-muted-foreground"> · heartbeat {c.heartbeat_age_s.toFixed(0)} s ago</span> : null}</div>
    </li>
  )
}

const FLOW = ["Devices / emulator", "Quality gate", "Vitals + Behaviour + Fall detectors", "Triage agent", "Case Investigator", "Care Coordinator", "Report agent"]

export default function HowItWorks() {
  const comps = useComponents()
  const msgs = useMessages(30)
  const { mode } = useMode()
  const det = comps.data?.filter((c) => c.kind === "detector") ?? []
  const ag = comps.data?.filter((c) => c.kind === "agent") ?? []
  const infra = comps.data?.filter((c) => c.kind === "infra") ?? []
  return (
    <div className="flex flex-col gap-5">
      <div><h2 className="text-xl font-semibold">How ECHOS works</h2>
        <p className="max-w-3xl text-sm text-muted-foreground">ECHOS watches the sensors and vital signs of one older person and tells their caregiver and clinician, at once when a fall or critical reading is detected, and once a day whether anything differs from that person's own usual. It supports decisions; it never diagnoses, and a language model never decides an emergency.</p></div>
      <Card><CardHeader><CardTitle>Data source right now: {MODE[mode].label}</CardTitle></CardHeader><CardContent className="text-sm">{MODE[mode].what}</CardContent></Card>
      <ol className="flex flex-wrap items-center gap-2 text-sm" aria-label="Pipeline" data-testid="pipeline">
        {FLOW.map((f, i) => <li key={f} className="flex items-center gap-2"><span className="rounded-lg border bg-card px-3 py-1.5 font-medium">{f}</span>{i < FLOW.length - 1 && <ArrowRight className="size-4 text-muted-foreground" aria-hidden="true" />}</li>)}
      </ol>
      {comps.isError && <ErrorNote error={comps.error} />}
      {!comps.data ? <CardSkeleton h="h-60" /> : (
        <>
          <section className="flex flex-col gap-2" aria-labelledby="det"><h2 id="det" className="text-lg font-semibold">Detectors <span className="text-sm font-normal text-muted-foreground">{KIND.detector.what}</span></h2>
            <ul className="grid gap-3 lg:grid-cols-3">{det.map((c) => <Card_ key={c.id} c={c} />)}</ul></section>
          <section className="flex flex-col gap-2" aria-labelledby="ag"><h2 id="ag" className="text-lg font-semibold">Agents <span className="text-sm font-normal text-muted-foreground">{KIND.agent.what}</span></h2>
            <ul className="grid gap-3 lg:grid-cols-2">{ag.map((c) => <Card_ key={c.id} c={c} />)}</ul></section>
          <section className="flex flex-col gap-2" aria-labelledby="inf"><h2 id="inf" className="text-lg font-semibold">Infrastructure</h2>
            <ul className="grid gap-3 lg:grid-cols-2">{infra.map((c) => <Card_ key={c.id} c={c} />)}</ul></section>
        </>)}
      <Card><CardHeader><CardTitle>Message bus: who told whom</CardTitle></CardHeader><CardContent>
        {!msgs.data?.length ? <p className="text-sm text-muted-foreground">No messages yet.</p> : (
          <ol className="flex flex-col gap-1 text-sm" data-testid="bus">{msgs.data.map((m) => (
            <li key={m.id} className="num"><strong>{m.sender}</strong> → {m.recipient} · {m.topic}{m.patient_id ? ` · ${personLabel(m.patient_id)}` : ""}{m.day ? ` · ${m.day}` : ""}</li>))}</ol>)}
      </CardContent></Card>
    </div>
  )
}
