import { Navigate, Route, Routes } from "react-router-dom"
import Layout from "@/components/Layout"
import Today from "@/pages/Today"
import Alerts from "@/pages/Alerts"
import HowItWorks from "@/pages/HowItWorks"
import FallMonitor from "@/pages/FallMonitor"
import PatientPage, { PatientRedirect } from "@/pages/PatientPage"
import Caregiver from "@/pages/Caregiver"
import Clinician from "@/pages/Clinician"
import System from "@/pages/System"

export default function App() {
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
        <Route path="clinician" element={<Clinician />} />
        <Route path="system" element={<System />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
