import AgentStrip from "@/components/AgentStrip"
import AlertFeed from "@/components/AlertFeed"
import PatientCard from "@/components/PatientCard"
import SimControls from "@/components/SimControls"
import { CardSkeleton, Empty, ErrorNote } from "@/components/States"
import { usePatients } from "@/lib/queries"

export default function Dashboard() {
  const patients = usePatients()
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">Dashboard</h1>
      <div className="grid gap-4 lg:grid-cols-2"><SimControls /><AgentStrip /></div>
      <div className="grid gap-6 lg:grid-cols-[1fr_20rem]">
        <section aria-labelledby="pts" className="flex flex-col gap-3">
          <h2 id="pts" className="text-lg font-semibold">Patients</h2>
          {patients.isLoading ? <div className="grid gap-3 sm:grid-cols-2"><CardSkeleton /><CardSkeleton /></div>
            : patients.isError ? <ErrorNote error={patients.error} />
            : patients.data!.items.length === 0 ? <Empty title="No patients yet">Press Play in the simulation controls to start the replay.</Empty>
            : <div className="grid gap-3 sm:grid-cols-2" data-testid="patient-cards">{patients.data!.items.map((p) => <PatientCard key={p.patient_id} p={p} />)}</div>}
        </section>
        <section aria-label="Live alerts"><AlertFeed /></section>
      </div>
    </div>
  )
}
