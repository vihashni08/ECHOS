import { useEffect, useMemo, useState } from "react"
import { Navigate, useNavigate, useParams } from "react-router-dom"
import { Info } from "lucide-react"
import BandBars from "@/charts/BandBars"
import Heatmap from "@/charts/Heatmap"
import VitalChart from "@/charts/VitalChart"
import TeamPlanPanel from "@/components/TeamPlanPanel"
import CaseFilePanel from "@/components/CaseFilePanel"
import { CardSkeleton, Empty, ErrorNote } from "@/components/States"
import Timeline from "@/components/Timeline"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { usePatients, useRoutine, useStatuses, useSummary, useVitals } from "@/lib/queries"
import { StatusBadge } from "@/lib/status"
import { fmtDay } from "@/lib/format"

const addDays = (d: string, n: number) => new Date(new Date(d).getTime() + n * 86400000).toISOString().slice(0, 10)

export function PatientRedirect() {
  const p = usePatients()
  if (p.isLoading) return <CardSkeleton h="h-24" />
  const first = p.data?.items[0]
  return first ? <Navigate to={`/patients/${first.patient_id}`} replace /> : <Empty title="No patients yet">Open Demo tools on the Today page and start the data source.</Empty>
}

function WhyFlagged({ pid }: { pid: string }) {
  const s = useSummary(pid)
  if (s.isLoading) return <CardSkeleton h="h-28" />
  if (s.isError) return <ErrorNote error={s.error} />
  const d = s.data!
  return (
    <Card data-testid="why-flagged">
      <CardHeader className="flex flex-row items-center justify-between gap-2"><CardTitle>Why flagged · <span className="num">{fmtDay(d.day)}</span></CardTitle><StatusBadge status={d.status} /></CardHeader>
      <CardContent className="flex flex-col gap-2 text-sm">
        {d.reasons.length === 0 ? <p>Nothing unusual compared with this person's own pattern.</p> : <ul className="list-disc space-y-1 pl-5">{d.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>}
        {d.data_note && <p className="flex items-start gap-1.5 text-muted-foreground"><Info className="mt-0.5 size-4 shrink-0" aria-hidden="true" />{d.data_note}</p>}
      </CardContent>
    </Card>
  )
}

export default function PatientPage() {
  const { id = "" } = useParams()
  const nav = useNavigate()
  const patients = usePatients()
  const me = patients.data?.items.find((p) => p.patient_id === id)
  const st = useStatuses(id, 120)
  const days = st.data?.map((d) => d.day) ?? []
  const min = days[0], max = days[days.length - 1]
  const [from, setFrom] = useState(""), [to, setTo] = useState("")
  useEffect(() => { if (max) { setTo(max); setFrom(min && addDays(max, -13) < min ? min : addDays(max, -13)) } }, [id, min, max])
  const ready = useMemo(() => !!from && !!to && from <= to, [from, to])
  const vit = useVitals(id, ready ? from : undefined, ready ? to : undefined)
  const rou = useRoutine(id, ready ? from : undefined, ready ? to : undefined)
  if (patients.isSuccess && !me) return <Empty title={`Unknown patient ${id}`} />
  const v = vit.data
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Patient <span className="num">{id}</span></h1>
          <p className="text-sm text-muted-foreground">{me ? `Age ${me.age_band} · ${me.sex}` : "…"}</p>
          <div className="mt-2 flex items-center gap-2 text-sm"><span className="text-muted-foreground">Last 14 days</span><Timeline pid={id} days={14} /></div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {me && <StatusBadge status={me.latest_status} />}
          <Select value={id} onValueChange={(v) => nav(`/patients/${v}`)}><SelectTrigger className="w-32" aria-label="Choose patient"><SelectValue /></SelectTrigger>
            <SelectContent>{patients.data?.items.map((p) => <SelectItem key={p.patient_id} value={p.patient_id}>{p.patient_id}</SelectItem>)}</SelectContent></Select>
        </div>
      </div>
      <WhyFlagged pid={id} />
      <CaseFilePanel pid={id} status={me?.latest_status} />
      <TeamPlanPanel pid={id} />
      <div className="flex flex-wrap items-end gap-3 text-sm">
        <label className="flex flex-col gap-1">From<input type="date" value={from} min={min} max={to || max} onChange={(e) => setFrom(e.target.value)} className="num h-9 rounded-md border bg-card px-2" aria-label="From date" /></label>
        <label className="flex flex-col gap-1">To<input type="date" value={to} min={from || min} max={max} onChange={(e) => setTo(e.target.value)} className="num h-9 rounded-md border bg-card px-2" aria-label="To date" /></label>
        <span className="text-muted-foreground">Data available {min ? `${min} to ${max}` : "n/a"}</span>
      </div>
      <Tabs defaultValue="vitals">
        <TabsList><TabsTrigger value="vitals">Vitals</TabsTrigger><TabsTrigger value="routine">Routine</TabsTrigger></TabsList>
        <TabsContent value="vitals" className="mt-3">
          {vit.isLoading || !v ? (vit.isError ? <ErrorNote error={vit.error} /> : <div className="grid gap-3 md:grid-cols-2"><CardSkeleton h="h-60" /><CardSkeleton h="h-60" /></div>) : (
            <div className="grid gap-3 md:grid-cols-2" data-testid="vitals-charts">
              <VitalChart title="Blood pressure" unit="mmHg" start={v.start} end={v.end} series={[{ s: v.series.sys, color: "var(--chart-1)", name: "Systolic" }, { s: v.series.dia, color: "var(--chart-2)", name: "Diastolic" }]} />
              <VitalChart title="Heart rate" unit="bpm" start={v.start} end={v.end} series={[{ s: v.series.hr, color: "var(--chart-1)", name: "Heart rate" }]} />
              <VitalChart title="Temperature" unit="°C" start={v.start} end={v.end} series={[{ s: v.series.temp, color: "var(--chart-1)", name: "Temperature" }]} />
              <VitalChart title="Weight" unit="kg" start={v.start} end={v.end} series={[{ s: v.series.weight, color: "var(--chart-1)", name: "Weight" }]} />
            </div>)}
        </TabsContent>
        <TabsContent value="routine" className="mt-3 flex flex-col gap-3">
          {!rou.data ? (rou.isError ? <ErrorNote error={rou.error} /> : <CardSkeleton h="h-72" />) : (
            <>
              <p className="flex items-start gap-1.5 text-sm text-muted-foreground"><Info className="mt-0.5 size-4 shrink-0" aria-hidden="true" />These charts describe unusual patterns against this person's own usual range. They are not a prediction.</p>
              <Heatmap r={rou.data} />
              <div className="grid gap-3 md:grid-cols-2" data-testid="routine-charts">
                <BandBars title="Night activity" unit="events per night" k="night_events" days={rou.data.days} note="personal baseline not yet established" />
                <BandBars title="Night bathroom visits" unit="visits per night" k="night_bathroom" days={rou.data.days} note="personal baseline not yet established" />
                <BandBars title="Total activity" unit="events per day" k="total_events" days={rou.data.days} note="personal baseline not yet established" />
              </div>
            </>)}
        </TabsContent>
      </Tabs>
    </div>
  )
}
