import { useState } from "react"
import { Pause, Play, RotateCcw } from "lucide-react"
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { usePatients, useSimControl, useSimState } from "@/lib/queries"

const SPEEDS = [0.5, 1, 5, 20, 100]

export default function SimControls() {
  const sim = useSimState()
  const patients = usePatients()
  const ctl = useSimControl()
  const [confirmReset, setConfirmReset] = useState(false)
  const [needReset, setNeedReset] = useState(false)
  const [msg, setMsg] = useState("")
  const s = sim.data
  const playing = s?.status === "playing"
  const storeHasData = (patients.data?.items ?? []).some((p) => p.latest_day)
  const run = (body: Parameters<typeof ctl.mutate>[0]) => { setMsg(""); ctl.mutate(body, { onError: (e) => setMsg((e as Error).message) }) }
  const onPlay = () => (s?.status === "idle" && (s.cursor ?? 0) === 0 && storeHasData ? setNeedReset(true) : run({ action: "play" }))
  const resetText = "This clears the simulation tables of the database the API was started with: events, agent outputs, day statuses, alerts, outbox rows, daily facts and reports. Patient demographics, models and report files on disk are kept."
  return (
    <Card>
      <CardHeader><CardTitle>Simulation</CardTitle></CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={playing ? () => run({ action: "pause" }) : onPlay} disabled={!s || ctl.isPending || s.status === "finished"} aria-label={playing ? "Pause simulation" : "Play simulation"} data-testid="play-pause">
            {playing ? <Pause className="size-4" aria-hidden="true" /> : <Play className="size-4" aria-hidden="true" />}{playing ? "Pause" : "Play"}
          </Button>
          <Select value={String(s?.speed ?? 20)} onValueChange={(v) => run({ action: "speed", speed: Number(v) })} disabled={!s}>
            <SelectTrigger className="w-32" aria-label="Simulation speed"><SelectValue /></SelectTrigger>
            <SelectContent>{[...new Set([...SPEEDS, s?.speed ?? 20])].sort((a, b) => a - b).map((v) => <SelectItem key={v} value={String(v)}>{v} events/s</SelectItem>)}</SelectContent>
          </Select>
          <Button variant="outline" onClick={() => setConfirmReset(true)} disabled={!s || ctl.isPending} aria-label="Reset simulation" data-testid="reset"><RotateCcw className="size-4" aria-hidden="true" />Reset</Button>
        </div>
        <p className="text-sm text-muted-foreground">{!s ? "Loading…" : s.status === "idle" ? `Idle: ${s.total ?? 0} events ready to replay.` : s.status === "playing" ? `Replaying: ${s.cursor} of ${s.total} events.` : s.status === "paused" ? `Paused at ${s.cursor} of ${s.total} events.` : ""}</p>
        {s?.status === "finished" && <p className="text-sm text-muted-foreground">All events were emitted. Reset to run again.</p>}
        {msg && <p role="alert" className="rounded-md border border-monitor-line bg-monitor-bg px-3 py-2 text-sm text-monitor-fg">{msg}</p>}
      </CardContent>
      <AlertDialog open={confirmReset} onOpenChange={setConfirmReset}>
        <AlertDialogContent>
          <AlertDialogHeader><AlertDialogTitle>Reset the simulation?</AlertDialogTitle><AlertDialogDescription>{resetText}</AlertDialogDescription></AlertDialogHeader>
          <AlertDialogFooter><AlertDialogCancel>Cancel</AlertDialogCancel><AlertDialogAction onClick={() => run({ action: "reset" })}>Reset and clear</AlertDialogAction></AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <AlertDialog open={needReset} onOpenChange={setNeedReset}>
        <AlertDialogContent>
          <AlertDialogHeader><AlertDialogTitle>A reset is needed before playing</AlertDialogTitle>
            <AlertDialogDescription>The store already holds events from an earlier run, so the simulation cannot start from the beginning. {resetText}</AlertDialogDescription></AlertDialogHeader>
          <AlertDialogFooter><AlertDialogCancel>Keep the data</AlertDialogCancel><AlertDialogAction onClick={() => ctl.mutate({ action: "reset" }, { onSuccess: () => ctl.mutate({ action: "play" }) })}>Reset and play</AlertDialogAction></AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  )
}
