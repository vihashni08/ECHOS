import { Info, Siren } from "lucide-react"
import DailyDoc, { type Doc } from "@/components/DailyDoc"
import { useEffect, useState } from "react"
import { useSearchParams } from "react-router-dom"
import PatientPicker from "@/components/PatientPicker"
import Provenance from "@/components/Provenance"
import SourceSheet from "@/components/SourceSheet"
import { CardSkeleton, Empty, ErrorNote } from "@/components/States"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { fmtDay } from "@/lib/format"
import { useDaily, usePatients, useStatuses, useSummary } from "@/lib/queries"
import { StatusBadge } from "@/lib/status"

export default function Caregiver() {
  const [sp, setSp] = useSearchParams()
  const patients = usePatients()
  const pid = sp.get("patient") ?? patients.data?.items[0]?.patient_id ?? ""
  const week = useStatuses(pid, 7)
  const strip = week.data ?? []
  const day = sp.get("day") ?? strip[strip.length - 1]?.day
  const summary = useSummary(pid, day)
  const daily = useDaily(pid, day)
  const [src, setSrc] = useState<string | null>(null)
  useEffect(() => { if (!sp.get("patient") && pid) setSp((p) => { p.set("patient", pid); return p }, { replace: true }) }, [pid, sp, setSp])
  const set = (patch: Record<string, string>) => setSp((p) => { Object.entries(patch).forEach(([k, v]) => v ? p.set(k, v) : p.delete(k)); return p })
  const d = daily.data
  const alerts = d?.alerts?.length ? d.alerts : summary.data?.alerts ?? []

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Caregiver</h1>
        <PatientPicker value={pid} onChange={(v) => set({ patient: v, day: "" })} />
      </div>
      {patients.isLoading ? <CardSkeleton h="h-64" /> : patients.data?.items.length === 0 ? <Empty title="No patients yet">Start the data source from Demo tools on the Today page.</Empty> : (
        <>
          {alerts.length > 0 && (
            <div role="alert" className="flex items-start gap-2.5 rounded-xl border border-urgent-line bg-urgent-bg px-4 py-3 text-urgent-fg" data-testid="alert-banner">
              <Siren className="mt-0.5 size-5 shrink-0" aria-hidden="true" />
              <ul className="space-y-0.5 font-medium">{alerts.map((a, i) => <li key={i}>{a}</li>)}</ul>
            </div>
          )}
          <Card data-testid="daily-card">
            <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
              <CardTitle>Today's report · <span className="num">{fmtDay(day)}</span></CardTitle>
              {summary.data && !d?.doc && <StatusBadge status={summary.data.status} className="text-base" />}
            </CardHeader>
            <CardContent className="flex flex-col gap-4 text-sm">
              {summary.isError ? <ErrorNote error={summary.error} /> : !summary.data || daily.isLoading ? <CardSkeleton h="h-40" /> : (
                d?.doc ? <DailyDoc doc={d.doc as unknown as Doc} onSource={setSrc} /> : (
                <>
                  <section aria-labelledby="chg"><h2 id="chg" className="mb-1 text-base font-semibold">What changed</h2>
                    <ul className="list-disc space-y-1 pl-5">{(d?.changes?.length ? d.changes : summary.data!.reasons.length ? summary.data!.reasons : ["Everything looked normal compared with this person's own usual pattern."]).map((c, i) => <li key={i}>{c}</li>)}</ul></section>
                  <section aria-labelledby="mon"><h2 id="mon" className="mb-1 text-base font-semibold">What to monitor today</h2>
                    {d?.monitor?.length ? <ul className="list-disc space-y-1 pl-5">{d.monitor.map((c, i) => <li key={i}>{c}</li>)}</ul> : <p className="text-muted-foreground">No stored report for this day, so no monitoring advice is available.</p>}</section>
                  {(d?.data_note ?? summary.data?.data_note) && <section aria-labelledby="dn"><h2 id="dn" className="mb-1 text-base font-semibold">Data note</h2>
                    <p className="flex items-start gap-1.5 text-muted-foreground"><Info className="mt-0.5 size-4 shrink-0" aria-hidden="true" />{d?.data_note ?? summary.data?.data_note}</p></section>}
                </>)
              )}
            </CardContent>
          </Card>
          <section aria-labelledby="wk" className="flex flex-col gap-2"><h2 id="wk" className="text-base font-semibold">Last 7 days</h2>
            {week.isLoading ? <CardSkeleton h="h-20" /> : strip.length === 0 ? <Empty title="No days yet" /> : (
              <ol className="grid grid-cols-7 gap-1.5" data-testid="week-strip">
                {strip.map((s) => (
                  <li key={s.day}><button onClick={() => set({ day: s.day })} aria-current={s.day === day} aria-label={`${s.day}: ${s.status}`}
                    className={`flex w-full flex-col items-center gap-1 rounded-lg border p-1.5 text-xs transition-colors hover:bg-secondary ${s.day === day ? "border-primary bg-accent" : "bg-card"}`}>
                    <span className="num">{s.day.slice(5)}</span><StatusBadge status={s.status} className="px-1.5 text-[11px] [&>span]:hidden sm:[&>span]:inline" />
                  </button></li>))}
              </ol>)}
          </section>
          {d && <Provenance p={d.provenance} mode={d.mode} onSource={setSrc} />}
        </>
      )}
      <SourceSheet id={src} onClose={() => setSrc(null)} />
    </div>
  )
}
