import { CheckCircle2, Clock, FlaskConical, Mail, XCircle } from "lucide-react"
import { useEffect, useState } from "react"
import { useSearchParams } from "react-router-dom"
import Waveform from "@/charts/Waveform"
import { Empty, ErrorNote } from "@/components/States"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import type { FallDetail, FallItem, Outbox } from "@/lib/api"
import { channelLabel, fmtTime } from "@/lib/format"
import { useFall, useFalls, usePatients, useSimFall } from "@/lib/queries"
import { StatusBadge } from "@/lib/status"

function Sent({ o }: { o: Outbox }) {
  const I = o.status === "SENT" ? CheckCircle2 : o.status === "FAILED" ? XCircle : Clock
  return <span className="inline-flex items-center gap-1 font-semibold"><I className="size-4" aria-hidden="true" />{o.status === "SENT" ? "Sent" : o.status === "FAILED" ? "Failed" : "Pending"}</span>
}

function AlertPanel({ d }: { d: FallDetail }) {
  return (
    <Card data-testid="alert-sent">
      <CardHeader><CardTitle className="flex items-center gap-2"><Mail className="size-4" aria-hidden="true" />Alert sent</CardTitle></CardHeader>
      <CardContent className="flex flex-col gap-3 text-sm">
        {!d.alert ? <p className="text-muted-foreground">No alert was raised for this fall: the post-fall inactivity was not confirmed, so the day stays at Monitor.</p> : (
          <>
            <ul className="flex flex-col divide-y rounded-md border">
              {d.outbox.map((o) => (
                <li key={o.outbox_id} data-testid="recipient" className="grid gap-x-4 gap-y-0.5 px-3 py-2 sm:grid-cols-[6rem_1fr_1fr_6rem] sm:items-center">
                  <span className="font-semibold capitalize">{o.recipient_role}</span>
                  <span className="num break-all">{o.address}</span>
                  <span>{channelLabel(o.channel)}</span>
                  <Sent o={o} />
                </li>))}
            </ul>
            {d.outbox[0] && (
              <div className="flex flex-col gap-1">
                <div><span className="text-muted-foreground">Subject: </span><span className="font-medium">{d.outbox[0].subject}</span></div>
                <pre tabIndex={0} aria-label="Message body" className="max-h-56 overflow-auto whitespace-pre-wrap rounded-md border bg-muted p-3 font-mono text-xs leading-relaxed">{d.outbox[0].body}</pre>
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  )
}

function Detail({ d }: { d: FallDetail }) {
  const need = d.inactivity_required_s ?? 3
  const obs = d.inactivity_observed_s ?? 0
  const yn = (b?: boolean | null) => (b == null ? "n/a" : b ? "yes" : "no")
  return (
    <div className="flex flex-col gap-4" data-testid="fall-detail">
      <Card>
        <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
          <CardTitle>Fall at <span className="num">{fmtTime(d.ts)}</span> · patient <span className="num">{d.patient_id}</span></CardTitle>
          <div className="flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center gap-1.5 rounded-full border border-dashed border-primary px-2.5 py-0.5 text-sm font-semibold text-primary" data-testid="sim-badge"><FlaskConical className="size-4" aria-hidden="true" />Simulated fall signal</span>
            <StatusBadge status={d.day_status} />
          </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {d.waveform ? <Waveform w={d.waveform} /> : <Empty title="Waveform unavailable">The SisFall recording is not on this machine.</Empty>}
          <div className="grid gap-4 sm:grid-cols-3">
            <div><div className="text-xs text-muted-foreground">Fall probability</div><div className="num text-3xl font-semibold" data-testid="prob">{d.probability_text}</div><div className="text-xs text-muted-foreground">flag threshold <span className="num">{d.threshold ?? 0.8}</span></div></div>
            <div><div className="text-xs text-muted-foreground">Peak impact</div><div className="num text-3xl font-semibold">{d.peak_g?.toFixed(1) ?? "n/a"}<span className="text-base font-normal"> g</span></div></div>
            <div>
              <div className="text-xs text-muted-foreground">Post-fall inactivity</div>
              <div className="mt-1 h-3 w-full overflow-hidden rounded-full bg-muted" role="progressbar" aria-valuemin={0} aria-valuemax={need} aria-valuenow={Math.min(obs, need)} aria-label="Observed still time against required">
                <div className="h-full rounded-full bg-primary" style={{ width: `${Math.min(100, (obs / need) * 100)}%` }} />
              </div>
              <div className="mt-1 text-sm"><span className="num">{obs.toFixed(1)} s</span> of <span className="num">{need.toFixed(1)} s</span> required: <strong>{d.inactivity ? "confirmed" : "not confirmed"}</strong></div>
            </div>
          </div>
        </CardContent>
      </Card>
      <AlertPanel d={d} />
      <Card>
        <CardContent className="flex flex-col gap-1 text-sm" data-testid="training-note">
          <p><strong>About this signal.</strong> TIHM has no falls, so this is a SisFall recording (scripted falls by volunteers on mats) injected into a real patient-day. {d.injected ? "It was injected from the UI." : "It came from the scheduled replay."}</p>
          <p>SisFall subject <span className="num font-semibold">{d.sisfall_subject ?? "n/a"}</span>. In the training set of the Fall worker's model: <strong>{yn(d.in_training_set_of_worker_model)}</strong>. In the training set of the final model (fall_v2): <strong>{yn(d.in_training_set_of_final_model)}</strong>.</p>
        </CardContent>
      </Card>
    </div>
  )
}

export default function FallMonitor() {
  const [sp, setSp] = useSearchParams()
  const patients = usePatients()
  const falls = useFalls()
  const sim = useSimFall()
  const pid = sp.get("patient") ?? ""
  const fid = sp.get("fall") ? Number(sp.get("fall")) : undefined
  const [simPid, setSimPid] = useState("")
  const [waiting, setWaiting] = useState<number | null>(null)
  const [note, setNote] = useState("")
  const ids = patients.data?.items.map((p) => p.patient_id) ?? []
  const mine: FallItem[] = (falls.data?.items ?? []).filter((f) => !pid || f.patient_id === pid)
  const detail = useFall(fid, waiting === fid)

  useEffect(() => { if (!simPid && ids[0]) setSimPid(ids[0]) }, [ids, simPid])
  useEffect(() => { if (fid === undefined && mine[0] && waiting === null) setSp((p) => { p.set("fall", String(mine[0].id)); return p }, { replace: true }) }, [fid, mine, waiting, setSp])
  useEffect(() => {
    if (waiting === null) return
    if (detail.data) { setWaiting(null); setNote("") }
    const t = setTimeout(() => { if (waiting !== null && !detail.data) { setWaiting(null); setNote("The Fall agent did not flag this window (its probability stayed below the threshold), so no alert was raised. Try again: some SisFall fall types are missed.") } }, 25000)
    return () => clearTimeout(t)
  }, [waiting, detail.data])

  const simulate = () => sim.mutate({ patient_id: simPid }, {
    onSuccess: (r) => { setNote(""); if (r.event_id != null) { setWaiting(r.event_id); setSp({ patient: r.patient_id, fall: String(r.event_id) }) } else setNote(r.note) },
    onError: (e) => setNote((e as Error).message),
  })

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h1 className="text-2xl font-semibold">Fall Monitor</h1>
        <div className="flex flex-wrap items-center gap-2">
          <Select value={simPid} onValueChange={setSimPid}><SelectTrigger className="w-36" aria-label="Patient to simulate a fall for"><SelectValue placeholder="Patient" /></SelectTrigger>
            <SelectContent>{ids.map((i) => <SelectItem key={i} value={i}>{i}</SelectItem>)}</SelectContent></Select>
          <Button onClick={simulate} disabled={!simPid || sim.isPending} data-testid="simulate-fall">{sim.isPending ? "Injecting…" : "Simulate fall"}</Button>
        </div>
      </div>
      {note && <p role="status" className="rounded-md border border-monitor-line bg-monitor-bg px-3 py-2 text-sm text-monitor-fg" data-testid="sim-note">{note}</p>}
      {waiting !== null && <p role="status" className="text-sm text-muted-foreground">Waiting for the Fall agent to score the new window…</p>}
      <div className="grid gap-4 lg:grid-cols-[16rem_1fr]">
        <div className="flex flex-col gap-2">
          <Select value={pid || "all"} onValueChange={(v) => setSp(v === "all" ? {} : { patient: v })}><SelectTrigger aria-label="Patient filter"><SelectValue /></SelectTrigger>
            <SelectContent><SelectItem value="all">All patients</SelectItem>{ids.map((i) => <SelectItem key={i} value={i}>{i}</SelectItem>)}</SelectContent></Select>
          {falls.isLoading ? <Skeleton className="h-40 w-full" /> : falls.isError ? <ErrorNote error={falls.error} />
            : mine.length === 0 ? <Empty title="No falls yet">Press Simulate fall to inject one.</Empty> : (
              <ul className="flex max-h-[32rem] flex-col gap-1.5 overflow-y-auto" aria-label="Falls" tabIndex={0} data-testid="fall-list">
                {mine.map((f) => (
                  <li key={f.id}><button onClick={() => setSp({ ...(pid ? { patient: pid } : {}), fall: String(f.id) })} aria-current={f.id === fid}
                    className={`w-full rounded-lg border p-2.5 text-left text-sm transition-colors hover:bg-secondary ${f.id === fid ? "border-primary bg-accent" : "bg-card"}`}>
                    <div className="flex items-center justify-between gap-2"><span className="num font-semibold">{f.patient_id}</span><span className="num text-xs text-muted-foreground">{fmtTime(f.ts)}</span></div>
                    <div className="mt-1 flex items-center justify-between gap-2"><span>probability <span className="num">{f.probability_text}</span></span><StatusBadge status={f.day_status} className="text-xs" /></div>
                  </button></li>))}
              </ul>)}
        </div>
        <div>
          {fid === undefined ? <Empty title="Select a fall">Pick a fall from the list to see its waveform and the alert it produced.</Empty>
            : detail.isLoading || (detail.isError && waiting === fid) ? <Skeleton className="h-96 w-full" />
            : detail.isError ? <ErrorNote error={detail.error} /> : <Detail d={detail.data!} />}
        </div>
      </div>
    </div>
  )
}
