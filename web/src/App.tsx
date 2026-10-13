import { Navigate, Route, Routes } from "react-router-dom"
import Layout from "@/components/Layout"
import Login from "@/pages/Login"
import { useMe } from "@/lib/queries"
import { atLeast, type Role } from "@/lib/auth"
import Today from "@/pages/Today"
import Alerts from "@/pages/Alerts"
import HowItWorks from "@/pages/HowItWorks"
import FallMonitor from "@/pages/FallMonitor"
import PatientPage, { PatientRedirect } from "@/pages/PatientPage"
import Caregiver from "@/pages/Caregiver"
import Clinician from "@/pages/Clinician"
import System from "@/pages/System"

function Need({ role, children }: { role: Role; children: React.ReactNode }) {
  const me = useMe()
  return !me.isSuccess || atLeast(me.data?.role, role) ? <>{children}</> : <div role="alert" className="rounded-md border border-monitor-line bg-monitor-bg px-3 py-2 text-sm text-monitor-fg" data-testid="forbidden">This page needs the {role} role.</div>
}

export default function App() {
  const me = useMe()
  if (me.isSuccess && me.data.auth_enabled && !me.data.role) return <Login />
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Today />} />
        <Route path="alerts" element={<Alerts />} />
        <Route path="how-it-works" element={<HowItWorks />} />
        <Route path="falls" element={<FallMonitor />} />
        <Route path="patient" element={<PatientRedirect />} />
        <Route path="patients/:id" element={<PatientPage />} />
        <Route path="caregiver" element={<Caregiver />} />
        <Route path="clinician" element={<Need role="clinician"><Clinician /></Need>} />
        <Route path="system" element={<Need role="operator"><System /></Need>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
