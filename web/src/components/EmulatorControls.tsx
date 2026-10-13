import { useState } from "react"
import { Pause, Play, Zap } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { useEmulator, usePatients } from "@/lib/queries"
import { useMode } from "@/lib/live"

const SPEEDS: [number, string][] = [[600, "1 data hour = 6 s"], [3600, "1 data hour = 1 s"], [14400, "1 data hour = 0.25 s"]]

/** The labelled device emulator: start / pause / pace / inject a synthetic fall. */
export default function EmulatorControls() {
  const { live } = useMode()
  const emu = useEmulator()
  const patients = usePatients()
  const [pid, setPid] = useState("")
  const [msg, setMsg] = useState("")
  const e = live.data?.emulator
  const pats = e?.patients ?? []
  const target = pid || pats[0] || patients.data?.items[0]?.patient_id || ""
  const run = (body: Parameters<typeof emu.mutate>[0], ok?: (r: { note?: string }) => string) => {
    setMsg(""); emu.mutate(body, { onSuccess: (r) => ok && setMsg(ok(r)), onError: (er) => setMsg((er as Error).message) })
  }
  const playing = e?.status === "playing"
  return (
    <section className="flex flex-col gap-3" aria-label="Device emulator" data-testid="emulator-controls">
      <p className="text-sm text-muted-foreground">The emulator replays real TIHM room-sensor and vitals events in wall-clock time. It is a stand-in for a device, not a device.</p>
      <div className="flex flex-wrap items-center gap-2">
        <Button onClick={() => run({ action: playing ? "pause" : "play" })} disabled={emu.isPending || e?.status === "finished"} data-testid="emu-play">
          {playing ? <Pause className="size-4" aria-hidden="true" /> : <Play className="size-4" aria-hidden="true" />}{playing ? "Pause" : e?.status === "paused" ? "Resume" : "Start"}
        </Button>
        <Select value={String(e?.speed ?? 3600)} onValueChange={(v) => run({ action: "speed", speed: Number(v) })} disabled={!e}>
          <SelectTrigger className="w-48" aria-label="Emulator pace"><SelectValue /></SelectTrigger>
          <SelectContent>{SPEEDS.map(([v, l]) => <SelectItem key={v} value={String(v)}>{l}</SelectItem>)}</SelectContent>
        </Select>
      </div>
      <p className="num text-sm" data-testid="emu-status">{!e ? "Emulator not loaded yet" : `${e.status} · data time ${e.sim_now?.replace("T", " ").slice(0, 16) ?? "n/a"} · ${e.cursor}/${e.total} events`}</p>
      <div className="flex flex-wrap items-center gap-2 border-t pt-3">
        <Select value={target} onValueChange={setPid}><SelectTrigger className="w-40" aria-label="Patient for the fall"><SelectValue placeholder="Patient" /></SelectTrigger>
          <SelectContent>{(pats.length ? pats : patients.data?.items.map((p) => p.patient_id) ?? []).map((p) => <SelectItem key={p} value={p}>Patient {p}</SelectItem>)}</SelectContent></Select>
        <Button variant="outline" onClick={() => run({ action: "fall", patient_id: target }, (r) => r.note ?? "Fall injected")} disabled={emu.isPending || !target || !playing && e?.status !== "paused"} data-testid="emu-fall"><Zap className="size-4" aria-hidden="true" />Inject a fall now</Button>
      </div>
      <p className="text-xs text-muted-foreground">SYNTHETIC: a SisFall recording from a held-out subject (young volunteers on mats), written as one accelerometer event at the patient's current data time.</p>
      {msg && <p role="status" className="rounded-md border px-3 py-2 text-sm" data-testid="emu-msg">{msg}</p>}
    </section>
  )
}
