import { Link } from "react-router-dom"
import { Card, CardContent } from "@/components/ui/card"
import { StatusBadge } from "@/lib/status"
import { fmtDay, fmtTime, typeLabel } from "@/lib/format"
import type { Patient } from "@/lib/api"

export default function PatientCard({ p }: { p: Patient }) {
  const c = p.completeness
  return (
    <Link to={`/patients/${p.patient_id}`} aria-label={`Open patient ${p.patient_id}`} className="block rounded-xl transition-shadow hover:shadow-md">
      <Card className="h-full">
        <CardContent className="flex flex-col gap-3">
          <div className="flex items-start justify-between gap-2">
            <div>
              <div className="num text-lg font-semibold">{p.patient_id}</div>
              <div className="text-sm text-muted-foreground">Age {p.age_band} · {p.sex}</div>
            </div>
            <StatusBadge status={p.latest_status} />
          </div>
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
            <dt className="text-muted-foreground">Latest day</dt><dd className="num">{fmtDay(p.latest_day)}</dd>
            <dt className="text-muted-foreground">Last alert</dt>
            <dd>{p.last_alert_type ? <>{typeLabel(p.last_alert_type)} <span className="num text-muted-foreground">{fmtTime(p.last_alert_time)}</span></> : "none"}</dd>
            <dt className="text-muted-foreground">Data</dt>
            <dd>vitals <span className="num">{c.days_with_vitals}/{c.days}</span> days · activity <span className="num">{c.days_with_activity}/{c.days}</span> days{c.sensor_gap_days ? <> · <span className="num">{c.sensor_gap_days}</span> sensor gap</> : null}</dd>
          </dl>
        </CardContent>
      </Card>
    </Link>
  )
}
