import { Navigate, Route, Routes } from "react-router-dom"
import Layout from "@/components/Layout"
import Login from "@/pages/Login"
import { useMe } from "@/lib/queries"
import { atLeast, type Role } from "@/lib/auth"
import Today from "@/pages/Today"
import DailyReview from "@/pages/DailyReview"
import Report from "@/pages/Report"
import Review from "@/pages/Review"
import Alerts from "@/pages/Alerts"
import Agents from "@/pages/Agents"
import HowItWorks from "@/pages/HowItWorks"
import Trace from "@/pages/Trace"
import TestYourself from "@/pages/TestYourself"
import PatientPage, { PatientRedirect } from "@/pages/PatientPage"

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
        <Route index element={<DailyReview />} />
        <Route path="briefing" element={<Today />} />
        <Route path="report" element={<Report />} />
        <Route path="system" element={<Need role="clinician"><Agents /></Need>} />
        <Route path="trace" element={<Trace />} />
        <Route path="trace/:pid" element={<Trace />} />
        <Route path="trace/:pid/:day" element={<Trace />} />
        <Route path="test" element={<TestYourself />} />
        <Route path="review" element={<Review />} />
        <Route path="alerts" element={<Alerts />} />
        <Route path="agents" element={<Need role="clinician"><Agents /></Need>} />
        <Route path="how" element={<HowItWorks />} />
        <Route path="patient" element={<PatientRedirect />} />
        <Route path="patients/:id" element={<PatientPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
