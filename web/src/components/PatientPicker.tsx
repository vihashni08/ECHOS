import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { usePatients } from "@/lib/queries"

export default function PatientPicker({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const p = usePatients()
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger className="w-40" aria-label="Choose patient" data-testid="patient-picker"><SelectValue placeholder="Patient" /></SelectTrigger>
      <SelectContent>{p.data?.items.map((x) => <SelectItem key={x.patient_id} value={x.patient_id}>{x.patient_id} · {x.age_band}</SelectItem>)}</SelectContent>
    </Select>
  )
}
