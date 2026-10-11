import { Navigate, Route, Routes } from "react-router-dom"
import Layout from "@/components/Layout"
import Dashboard from "@/pages/Dashboard"
import FallMonitor from "@/pages/FallMonitor"
import PatientPage, { PatientRedirect } from "@/pages/PatientPage"
import Caregiver from "@/pages/Caregiver"
import Clinician from "@/pages/Clinician"
import System from "@/pages/System"

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="falls" element={<FallMonitor />} />
        <Route path="patient" element={<PatientRedirect />} />
        <Route path="patients/:id" element={<PatientPage />} />
        <Route path="caregiver" element={<Caregiver />} />
        <Route path="clinician" element={<Clinician />} />
        <Route path="system" element={<System />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
