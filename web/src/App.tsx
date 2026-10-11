import { Navigate, Route, Routes } from "react-router-dom"
import Layout from "@/components/Layout"
import Dashboard from "@/pages/Dashboard"
import FallMonitor from "@/pages/FallMonitor"
import PatientPage, { PatientRedirect } from "@/pages/PatientPage"
import Placeholder from "@/pages/Placeholder"

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="falls" element={<FallMonitor />} />
        <Route path="patient" element={<PatientRedirect />} />
        <Route path="patients/:id" element={<PatientPage />} />
        <Route path="caregiver" element={<Placeholder title="Caregiver" />} />
        <Route path="clinician" element={<Placeholder title="Clinician" />} />
        <Route path="system" element={<Placeholder title="System" />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
