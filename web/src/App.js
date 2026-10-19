import { Fragment as _Fragment, jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "@/components/Layout";
import Login from "@/pages/Login";
import { useMe } from "@/lib/queries";
import { atLeast } from "@/lib/auth";
import Today from "@/pages/Today";
import Alerts from "@/pages/Alerts";
import FallMonitor from "@/pages/FallMonitor";
import PatientPage, { PatientRedirect } from "@/pages/PatientPage";
import Caregiver from "@/pages/Caregiver";
import Clinician from "@/pages/Clinician";
import System from "@/pages/System";
function Need({ role, children }) {
    const me = useMe();
    return !me.isSuccess || atLeast(me.data?.role, role) ? _jsx(_Fragment, { children: children }) : _jsxs("div", { role: "alert", className: "rounded-md border border-monitor-line bg-monitor-bg px-3 py-2 text-sm text-monitor-fg", "data-testid": "forbidden", children: ["This page needs the ", role, " role."] });
}
export default function App() {
    const me = useMe();
    if (me.isSuccess && me.data.auth_enabled && !me.data.role)
        return _jsx(Login, {});
    return (_jsx(Routes, { children: _jsxs(Route, { element: _jsx(Layout, {}), children: [_jsx(Route, { index: true, element: _jsx(Today, {}) }), _jsx(Route, { path: "alerts", element: _jsx(Alerts, {}) }), _jsx(Route, { path: "agents", element: _jsx(Navigate, { to: "/system?tab=agents", replace: true }) }), _jsx(Route, { path: "how-it-works", element: _jsx(Navigate, { to: "/system?tab=how", replace: true }) }), _jsx(Route, { path: "falls", element: _jsx(FallMonitor, {}) }), _jsx(Route, { path: "patient", element: _jsx(PatientRedirect, {}) }), _jsx(Route, { path: "patients/:id", element: _jsx(PatientPage, {}) }), _jsx(Route, { path: "caregiver", element: _jsx(Caregiver, {}) }), _jsx(Route, { path: "clinician", element: _jsx(Need, { role: "clinician", children: _jsx(Clinician, {}) }) }), _jsx(Route, { path: "system", element: _jsx(System, {}) }), _jsx(Route, { path: "*", element: _jsx(Navigate, { to: "/", replace: true }) })] }) }));
}
