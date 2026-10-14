import { Download, ExternalLink, Eye } from "lucide-react"
import { useEffect, useMemo, useRef, useState } from "react"
import { useSearchParams } from "react-router-dom"
import PatientPicker from "@/components/PatientPicker"
import SendWeekly, { DeliveryStatus } from "@/components/SendWeekly"
import SourceSheet from "@/components/SourceSheet"
import { CardSkeleton, Empty, ErrorNote } from "@/components/States"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { API_URL } from "@/lib/api"
import { fmtEpoch, isoWeek } from "@/lib/format"
import { useBuildWeekly, useOutbox, usePatients, useStatuses, useWeekly, useWeeklyAvailable } from "@/lib/queries"

type Row = { date: string; time: string; type: string; detail: string }
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function alertRows(d: any): Row[] {
  const rows: Row[] = []
  for (const f of d.falls ?? []) rows.push({ date: f.day, time: f.time ?? "", type: "Possible fall", detail: `Fall probability ${f.probability_text}; peak ${f.peak_g ?? "n/a"} g; inactivity afterwards: ${f.inactivity ? "yes" : "no"}` })
  for (const a of d.vitals_alerts ?? []) {
    const parts = [["sys", "Systolic BP"], ["dia", "Diastolic BP"], ["hr", "Heart rate"]].flatMap(([k, n]) => a[`${k}_now`] != null && a[`${k}_baseline`] != null ? [`${n} ${a[`${k}_now`]} ${a[`${k}_now`] > a[`${k}_baseline`] ? "above" : "below"} usual ${a[`${k}_baseline`]}`] : [])
    rows.push({ date: a.day, time: "", type: a.kind === "critical" ? "Critical vitals" : "Vitals alert", detail: a.kind === "critical" ? `Critical-range reading: sys ${a.sys ?? "n/a"}, dia ${a.dia ?? "n/a"}, hr ${a.hr ?? "n/a"}` : parts.join("; ") || "Vitals differ from usual" })
  }
  for (const u of d.unusual_days ?? []) {
    const z = Object.entries(u.z ?? {}).filter(([, v]) => v != null).map(([k, v]) => `${k.replace(/_/g, " ")} z ${(v as number) > 0 ? "+" : ""}${v}`).join(", ")
    rows.push({ date: u.day, time: "", type: "Unusual activity pattern", detail: `Activity pattern differs from usual${z ? `: ${z}` : ""}` })
  }
  return rows.sort((a, b) => (a.date + a.time).localeCompare(b.date + b.time))
}

export default function Clinician() {
  const [sp, setSp] = useSearchParams()
  const patients = usePatients()
  const pid = sp.get("patient") ?? patients.data?.items[0]?.patient_id ?? ""
  const days = useStatuses(pid, 120)
  const weeks = useMemo(() => [...new Set((days.data ?? []).map((d) => isoWeek(d.day)))], [days.data])
  const week = sp.get("week") ?? weeks[weeks.length - 1] ?? ""
  const avail = useWeeklyAvailable(pid)
  const stored = avail.data?.includes(week) ?? false
  const weekly = useWeekly(pid, week, stored)
  const build = useBuildWeekly()
  const built = useRef("")
  const [src, setSrc] = useState<string | null>(null)
  const outbox = useOutbox(15)
  useEffect(() => { if (!sp.get("patient") && pid) setSp((p) => { p.set("patient", pid); return p }, { replace: true }) }, [pid, sp, setSp])
  useEffect(() => {   // no stored weekly report for this week yet: build it (nothing is emailed)
    const key = `${pid}|${week}`
    if (pid && week && avail.isSuccess && !stored && built.current !== key) { built.current = key; build.mutate({ pid, week }) }
  }, [pid, week, avail.isSuccess, stored, build])
  const w = weekly.data
  const d = w?.data as Record<string, any> | null | undefined // eslint-disable-line @typescript-eslint/no-explicit-any
  const pdf = `${API_URL}/api/patients/${pid}/reports/weekly/pdf?week=${week}`
  const rows = d ? alertRows(d) : []
  const sources = ((w?.provenance as { sources_retrieved?: { id: string; title: string; source: string }[] } | null)?.sources_retrieved) ?? []
  const set = (k: string, v: string) => setSp((p) => { p.set(k, v); return p })

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Clinician</h1>
        <div className="flex flex-wrap items-center gap-2">
          <PatientPicker value={pid} onChange={(v) => setSp({ patient: v })} />
          <Select value={week} onValueChange={(v) => set("week", v)}><SelectTrigger className="w-36" aria-label="Choose week" data-testid="week-picker"><SelectValue placeholder="Week" /></SelectTrigger>
            <SelectContent>{[...weeks].reverse().map((x) => <SelectItem key={x} value={x}>{x}</SelectItem>)}</SelectContent></Select>
        </div>
      </div>
      {!pid || !week ? (patients.isLoading || days.isLoading ? <CardSkeleton h="h-64" /> : <Empty title="No weeks yet">Weekly reports appear once the simulation has processed a few days.</Empty>)
        : weekly.isError ? <ErrorNote error={weekly.error} />
        : !w ? (build.isError ? <ErrorNote error={build.error} /> : <div role="status" aria-label="Building the weekly report"><CardSkeleton h="h-64" /><p className="mt-2 text-sm text-muted-foreground">Building the weekly report… (nothing is emailed)</p></div>)
        : (
          <>
            <div className="grid grid-cols-2 gap-2 md:grid-cols-5" data-testid="tiles">
              {[
                ["Days by status", d ? `Normal ${d.status_counts.NORMAL} · Monitor ${d.status_counts.MONITOR} · Urgent ${d.status_counts.URGENT}` : "n/a", d ? `data gap ${d.status_counts.DATA_GAP}${(d.status_counts as Record<string, number>).NOT_PROCESSED ? ` · not yet processed ${(d.status_counts as Record<string, number>).NOT_PROCESSED}` : ""}` : ""],
                ["Vitals alerts", String(d?.vitals_alerts?.length ?? 0), ""], ["Falls", String(d?.falls?.length ?? 0), ""], ["Unusual-pattern days", String(d?.unusual_days?.length ?? 0), ""],
                ["Completeness", d ? `vitals ${d.completeness.days_with_vitals}/7 · activity ${d.completeness.days_with_activity}/7` : "n/a", d?.partial_week_note ?? ""],
              ].map(([t, v, s]) => (
                <Card key={t} className="py-3"><CardContent className="flex flex-col gap-0.5 px-3"><div className="text-xs text-muted-foreground">{t}</div><div className="text-sm font-semibold leading-snug">{v}</div>{s && <div className="text-xs text-muted-foreground">{s}</div>}</CardContent></Card>
              ))}
            </div>
            <Card>
              <CardHeader><CardTitle>Key findings · <span className="num">{w.week_start} to {w.week_end}</span></CardTitle></CardHeader>
              <CardContent className="flex flex-col gap-3 text-sm">
                <p data-testid="narrative" className="leading-relaxed">{w.narrative ?? "No narrative stored."}</p>
                <p className="text-xs text-muted-foreground">Written by {w.mode === "llm" ? "an AI model, checked by an automatic validator" : "a fixed template"}.</p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle>Alerts</CardTitle></CardHeader>
              <CardContent>
                {rows.length === 0 ? <p className="text-sm text-muted-foreground">No alerts this week.</p> : (
                  <div className="overflow-x-auto"><table className="w-full text-left text-sm" data-testid="alerts-table">
                    <thead className="text-xs text-muted-foreground"><tr><th className="pb-1 pr-3 font-medium">Date</th><th className="pb-1 pr-3 font-medium">Time</th><th className="pb-1 pr-3 font-medium">Type</th><th className="pb-1 font-medium">Detail</th></tr></thead>
                    <tbody>{rows.map((r, i) => <tr key={i} className="border-t align-top"><td className="num py-1.5 pr-3">{r.date}</td><td className="num py-1.5 pr-3">{r.time}</td><td className="py-1.5 pr-3 font-medium">{r.type}</td><td className="py-1.5">{r.detail}</td></tr>)}</tbody>
                  </table></div>)}
                {d && <p className="mt-3 text-xs text-muted-foreground" data-testid="dq-line">Data quality: room-sensor activity on {d.completeness.days_with_activity} of 7 days, vitals on {d.completeness.days_with_vitals} of 7 days, {d.completeness.sensor_gap_days} sensor-gap day(s), {d.completeness.invalid_values_removed} invalid value(s) removed, longest stretch without vitals {d.completeness.longest_vitals_gap_days} day(s).{d.partial_week_note ? ` ${d.partial_week_note}` : ""}</p>}
              </CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle>References (for clinician review)</CardTitle></CardHeader>
              <CardContent>
                {sources.length === 0 ? <p className="text-sm text-muted-foreground">No guideline sources were retrieved for this week.</p> : (
                  <ul className="flex flex-col gap-1.5 text-sm" data-testid="references">{sources.map((s) => (
                    <li key={s.id}><button onClick={() => setSrc(s.id)} className="flex w-full items-start gap-2 rounded-md border px-3 py-2 text-left hover:bg-secondary" aria-label={`Open source ${s.id}: ${s.title}`}>
                      <span className="num shrink-0 font-semibold text-primary">[{s.id}]</span><span>{s.title} <span className="text-muted-foreground">· {s.source}</span></span></button></li>))}</ul>)}
              </CardContent>
            </Card>
            <div className="flex flex-wrap items-start gap-3">
              <Button asChild variant="outline"><a href={pdf} download={`${pid}_${week}.pdf`} data-testid="download-pdf"><Download className="size-4" aria-hidden="true" />Download PDF</a></Button>
              <Button asChild variant="outline"><a href={pdf} target="_blank" rel="noreferrer noopener" data-testid="preview-pdf"><Eye className="size-4" aria-hidden="true" />Preview PDF<ExternalLink className="size-3.5" aria-hidden="true" /></a></Button>
              <SendWeekly pid={pid} week={week} />
            </div>
          </>
        )}
      <Card>
        <CardHeader><CardTitle>Outbox <span className="text-sm font-normal text-muted-foreground">(newest 8)</span></CardTitle></CardHeader>
        <CardContent>
          {!outbox.data?.items.length ? <p className="text-sm text-muted-foreground">Nothing in the outbox yet.</p> : (
            <ul className="flex flex-col divide-y text-sm" data-testid="outbox-list">
              {outbox.data.items.slice(0, 8).map((o) => (
                <li key={o.outbox_id} className="flex flex-col gap-0.5 py-2 sm:flex-row sm:items-center sm:gap-4">
                  <span className="num text-muted-foreground sm:w-28">{fmtEpoch(o.queued_time)}</span>
                  <span className="min-w-0 flex-1 break-words font-medium">{o.kind === "weekly" ? `Weekly report${o.attachment_name ? ` (${o.attachment_name})` : ""}` : "Alert"}</span>
                  <span><span className="capitalize">{o.recipient_role}</span> · <span className="num">{o.address}</span></span>
                  <span className="flex items-center gap-3"><DeliveryStatus o={o} /><span>{o.delivery === "dry_run" ? "Dry run" : "Email"}</span></span>
                </li>))}
            </ul>)}
        </CardContent>
      </Card>
      <SourceSheet id={src} onClose={() => setSrc(null)} />
    </div>
  )
}
