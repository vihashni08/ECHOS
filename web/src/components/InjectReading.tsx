import { useState } from "react"
import { Syringe } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { useInjectReading, usePatients } from "@/lib/queries"
import { personLabel } from "@/lib/live"

const PRESETS = [
  { key: "bp", label: "Severe high BP 192/124", body: { sys: 192, dia: 124, hr: 84 } },
  { key: "low", label: "Low BP 86/52", body: { sys: 86, dia: 52, hr: 92 } },
  { key: "pulse", label: "Slow pulse 38", body: { sys: 132, dia: 78, hr: 38 } },
  { key: "ok", label: "Ordinary reading 128/78", body: { sys: 128, dia: 78, hr: 70 } },
] as const

/** Demo: one reading for a replay patient, now. The Patient Monitor checks it at once; a red flag becomes an URGENT alert via the Care agent. */
export default function InjectReading() {
  const patients = usePatients()
  const inj = useInjectReading()
  const ids = (patients.data?.items ?? []).map((p) => p.patient_id)
  const [pid, setPid] = useState("")
  const [preset, setPreset] = useState<(typeof PRESETS)[number]["key"]>("bp")
  const [msg, setMsg] = useState("")
  const who = pid || ids[0] || ""
  const p = PRESETS.find((x) => x.key === preset)!
  return (
    <Card>
      <CardHeader><CardTitle>Inject a reading</CardTitle></CardHeader>
      <CardContent className="flex flex-col gap-3 text-sm">
        <p className="text-muted-foreground">The only invented data in the demo: one reading at the current replay time, marked as injected.</p>
        <div className="flex flex-wrap gap-2">
          <Select value={who} onValueChange={setPid} disabled={!ids.length}>
            <SelectTrigger className="w-40" aria-label="Patient"><SelectValue placeholder="Patient" /></SelectTrigger>
            <SelectContent>{ids.map((id) => <SelectItem key={id} value={id}>{personLabel(id)}</SelectItem>)}</SelectContent>
          </Select>
          <Select value={preset} onValueChange={(v) => setPreset(v as typeof preset)}>
            <SelectTrigger className="w-56" aria-label="Reading"><SelectValue /></SelectTrigger>
            <SelectContent>{PRESETS.map((x) => <SelectItem key={x.key} value={x.key}>{x.label}</SelectItem>)}</SelectContent>
          </Select>
          <Button onClick={() => { setMsg(""); inj.mutate({ patient_id: who, ...p.body }, { onSuccess: (r) => setMsg(`Sent to the Patient Monitor (event ${(r as { event_id?: number }).event_id ?? "queued"}).`), onError: (e) => setMsg((e as Error).message) }) }}
            disabled={!who || inj.isPending} data-testid="inject-reading"><Syringe className="size-4" aria-hidden="true" />Inject</Button>
        </div>
        {msg && <p role="status" className="text-sm" data-testid="inject-result">{msg}</p>}
      </CardContent>
    </Card>
  )
}
