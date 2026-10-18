import { AlertTriangle, CheckCircle2 } from "lucide-react"
import { useSearchParams } from "react-router-dom"
import AgentActivity from "@/pages/AgentActivity"
import HowItWorks from "@/pages/HowItWorks"
import { atLeast } from "@/lib/auth"
import { CardSkeleton, ErrorNote } from "@/components/States"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { fmt3, fmtCI } from "@/lib/format"
import { useAgents, useBudget, useLimitations, useMe, useMetrics, useQuality } from "@/lib/queries"

/* eslint-disable @typescript-eslint/no-explicit-any */
const Th = ({ children }: { children: React.ReactNode }) => <th className="pb-1 pr-3 font-medium">{children}</th>
const Table = ({ head, children, label }: { head: string[]; children: React.ReactNode; label: string }) => (
  <div className="overflow-x-auto"><table className="w-full text-left text-sm" aria-label={label}><thead className="text-xs text-muted-foreground"><tr>{head.map((h, i) => <Th key={`${h}-${i}`}>{h}</Th>)}</tr></thead><tbody>{children}</tbody></table></div>)
const Td = ({ children, ...p }: { children: React.ReactNode } & React.HTMLAttributes<HTMLTableCellElement>) => <td className="num border-t py-1.5 pr-3" {...p}>{children}</td>

function Health() {
  const agents = useAgents(), budget = useBudget()
  return (
    <div className="flex flex-col gap-4">
      <Card><CardHeader><CardTitle>Agent heartbeats</CardTitle></CardHeader><CardContent>
        {!agents.data ? <CardSkeleton h="h-20" /> : (
          <Table label="Agent heartbeats" head={["Agent", "State", "Heartbeat age", "Last event", "Supervisor restarts"]}>
            {agents.data.map((a) => <tr key={a.agent}><td className="border-t py-1.5 pr-3 font-medium">{a.agent}</td>
              <td className="border-t py-1.5 pr-3"><span className="inline-flex items-center gap-1 font-semibold">{a.alive ? <CheckCircle2 className="size-4" aria-hidden="true" /> : <AlertTriangle className="size-4" aria-hidden="true" />}{a.alive ? "alive" : "stale"}</span></td>
              <Td>{a.heartbeat_age_s == null ? "n/a" : `${a.heartbeat_age_s.toFixed(0)} s`}</Td><Td>{a.last_event_id ?? "n/a"}</Td><Td data-testid={`restarts-${a.agent}`}>{a.restarts}</Td></tr>)}
          </Table>)}
      </CardContent></Card>
      <Card><CardHeader><CardTitle>LLM budget</CardTitle></CardHeader><CardContent className="flex flex-col gap-2">
        {!budget.data ? <CardSkeleton h="h-20" /> : (
          <>
            <Table label="LLM budget per model" head={["Model", "Per minute (cap)", "Last 24 h", "Failures", "Cooldown"]}>
              {budget.data.models.map((m) => <tr key={m.model}><td className="border-t py-1.5 pr-3 font-medium">{m.model}</td><Td>{m.requests_last_minute} / {m.effective_rpm_cap ?? "n/a"}</Td><Td>{m.requests_last_24h}</Td><Td>{m.failures_last_24h}</Td><Td>{m.cooldown_remaining_s > 0 ? `${m.cooldown_remaining_s.toFixed(0)} s` : "none"}</Td></tr>)}
            </Table>
            <p className="text-sm text-muted-foreground">Cache hits <span className="num">{budget.data.cache_hits}</span> · model requests <span className="num">{budget.data.model_requests}</span> · hit rate <span className="num">{budget.data.cache_hit_rate ?? "n/a"}</span>. The cap is 20% below each model's limit.</p>
          </>)}
      </CardContent></Card>
    </div>
  )
}

function Quality() {
  const q = useQuality()
  if (q.isLoading) return <CardSkeleton h="h-40" />
  if (q.isError) return <ErrorNote error={q.error} />
  const l = q.data!.live as Record<string, number>
  const rows: [string, unknown][] = [["Patient-days processed", l.patient_days], ["Days with room-sensor activity", l.days_with_activity], ["Days with vitals readings", l.days_with_vitals], ["Sensor-gap days", l.sensor_gap_days], ["Invalid values removed", l.invalid_values_removed], ["Days passed through unchecked", l.days_unchecked]]
  return (
    <Card><CardHeader><CardTitle>Data Quality agent</CardTitle></CardHeader><CardContent>
      <dl className="grid grid-cols-[1fr_auto] gap-x-6 gap-y-1.5 text-sm sm:max-w-md" data-testid="quality">
        {rows.map(([k, v]) => <div key={k} className="contents"><dt className="text-muted-foreground">{k}</dt><dd className="num text-right font-semibold">{String(v)}</dd></div>)}
      </dl>
      {l.patient_days > 0 && <p className="mt-3 text-sm text-muted-foreground">Activity coverage {((l.days_with_activity / l.patient_days) * 100).toFixed(0)}% of days, vitals coverage {((l.days_with_vitals / l.patient_days) * 100).toFixed(0)}% of days.</p>}
    </CardContent></Card>
  )
}

function Models() {
  const m = useMetrics()
  if (m.isLoading) return <CardSkeleton h="h-64" />
  if (m.isError) return <ErrorNote error={m.error} />
  const f = m.data!.files as Record<string, any>
  const ms = f.model_selection ?? {}, cal = f.calibration ?? {}, audit = f.fall_audit ?? {}
  const elder = ms.fall_elderly_by_threshold
  return (
    <div className="flex flex-col gap-4" data-testid="models">
      {(["vitals", "behaviour"] as const).map((k) => ms[k] && (
        <Card key={k}><CardHeader><CardTitle>{k === "vitals" ? "Vitals model" : "Behaviour model"} · patient-level cross-validation</CardTitle></CardHeader><CardContent className="flex flex-col gap-2">
          <Table label={`${k} model selection`} head={["Model", "PR-AUC (95% CI)", "Recall at alert budget (95% CI)", "False alerts / patient-week (95% CI)"]}>
            {Object.entries(ms[k].models as Record<string, any>).map(([name, r]) => (
              <tr key={name}><td className="border-t py-1.5 pr-3 font-medium">{name}</td><Td data-testid={`${k}-${name}-prauc`}>{fmtCI(r.pr_auc)}</Td><Td>{fmtCI(r.recall)}</Td><Td>{fmtCI(r.fa_pw)}</Td></tr>))}
          </Table>
          <p className="text-xs text-muted-foreground">{ms[k].n_patients} patients, {ms[k].n_rows} patient-days, prevalence {fmt3(ms[k].prevalence)}. Alert budget: {ms[k].alert_budget}.</p>
        </CardContent></Card>))}
      {(cal.vitals || cal.behaviour) && (
        <Card><CardHeader><CardTitle>Calibration (Brier score, lower is better)</CardTitle></CardHeader><CardContent>
          <Table label="Calibration" head={["Model", "Always predict prevalence", "Raw", "Isotonic", "Platt"]}>
            {(["vitals", "behaviour"] as const).filter((k) => cal[k]).map((k) => <tr key={k}><td className="border-t py-1.5 pr-3 font-medium capitalize">{k}</td><Td>{fmt3(cal[k].brier_baseline_prevalence)}</Td><Td data-testid={`${k}-brier-raw`}>{fmt3(cal[k].brier_raw)}</Td><Td>{fmt3(cal[k].brier?.isotonic)}</Td><Td>{fmt3(cal[k].brier?.platt)}</Td></tr>)}
          </Table></CardContent></Card>)}
      {audit.curve && (
        <Card><CardHeader><CardTitle>Fall detector: threshold curve (adult subjects)</CardTitle></CardHeader><CardContent className="flex flex-col gap-3">
          <Table label="Fall threshold curve" head={["Threshold", "Adult sensitivity", "False alarms / h", "With post-fall inactivity: sensitivity", "False alarms / h"]}>
            {(audit.thresholds as number[]).map((t) => {
              const a = audit.curve.find((c: any) => c.threshold === t && !c.confirm_inactivity), b = audit.curve.find((c: any) => c.threshold === t && c.confirm_inactivity)
              return <tr key={t} className={t === audit.chosen?.threshold ? "bg-accent" : ""}><Td>{t}{t === audit.chosen?.threshold ? " (chosen)" : ""}</Td><Td data-testid={`curve-${t}`}>{fmt3(a?.adult_sensitivity)}</Td><Td>{a?.adult_false_alarms_per_hour?.toFixed(2)}</Td><Td>{fmt3(b?.adult_sensitivity)}</Td><Td>{b?.adult_false_alarms_per_hour?.toFixed(2)}</Td></tr>
            })}
          </Table>
          {elder && (
            <>
              <h3 className="text-sm font-semibold">Elderly sensitivity (subject {elder.subjects.join(", ")}, {elder.n_falls} falls)</h3>
              <Table label="Elderly sensitivity by threshold" head={["Threshold", "Without inactivity confirmation", "With inactivity confirmation"]}>
                {elder.rows.map((r: any) => <tr key={r.threshold}><Td>{r.threshold}</Td><Td data-testid={`elder-${r.threshold}-no`}>{fmt3(r.without_inactivity.sensitivity)} ({r.without_inactivity.falls_detected}/{elder.n_falls})</Td><Td>{fmt3(r.with_inactivity.sensitivity)} ({r.with_inactivity.falls_detected}/{elder.n_falls})</Td></tr>)}
              </Table>
              <p className="text-xs text-muted-foreground">One elderly subject only: these numbers say nothing about elderly people in general.</p>
            </>)}
        </CardContent></Card>)}
    </div>
  )
}

function Limitations() {
  const l = useLimitations()
  return (
    <Card><CardHeader><CardTitle>Limitations</CardTitle></CardHeader><CardContent className="flex flex-col gap-3 text-sm">
      {l.isLoading ? <CardSkeleton h="h-32" /> : l.isError ? <ErrorNote error={l.error} /> : <div className="flex flex-col gap-1.5" data-testid="limitations">{l.data!.text.split("\n").filter(Boolean).map((t, i) => <p key={i}>{t}</p>)}</div>}
      <p className="rounded-md border border-monitor-line bg-monitor-bg px-3 py-2 text-monitor-fg">Fall signals in this demo are simulated (SisFall recordings injected into TIHM patient-days). During an agent outage a fall alert is delayed by up to the restart time (about 12 to 15 s) but is not lost: events queue and are processed after the restart.</p>
    </CardContent></Card>
  )
}

export default function System() {
  const me = useMe()
  const operator = !me.isSuccess || atLeast(me.data?.role, "operator")        // the health / metrics tabs read operator-only endpoints
  const [params, setParams] = useSearchParams()
  const tab = params.get("tab") ?? (operator ? "health" : "how")
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold">System</h1>
      <Tabs value={tab} onValueChange={(v) => setParams({ tab: v }, { replace: true })}>
        <TabsList className="h-auto flex-wrap">
          <TabsTrigger value="how">How it works</TabsTrigger><TabsTrigger value="agents">Agent activity</TabsTrigger>
          {operator && <><TabsTrigger value="health">Health</TabsTrigger><TabsTrigger value="quality">Data quality</TabsTrigger><TabsTrigger value="models">Models</TabsTrigger><TabsTrigger value="limits">Limitations</TabsTrigger></>}
        </TabsList>
        <TabsContent value="how" className="mt-3"><HowItWorks /></TabsContent>
        <TabsContent value="agents" className="mt-3"><AgentActivity /></TabsContent>
        {operator && <>
          <TabsContent value="health" className="mt-3"><Health /></TabsContent>
          <TabsContent value="quality" className="mt-3"><Quality /></TabsContent>
          <TabsContent value="models" className="mt-3"><Models /></TabsContent>
          <TabsContent value="limits" className="mt-3"><Limitations /></TabsContent>
        </>}
      </Tabs>
    </div>
  )
}
