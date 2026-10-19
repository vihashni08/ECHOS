import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useQueryClient } from "@tanstack/react-query";
import { Activity, BellRing, LogOut, HeartPulse, LayoutDashboard, Moon, Settings2, Stethoscope, Sun, User, Users, WifiOff } from "lucide-react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { Button } from "@/components/ui/button";
import ErrorBoundary from "./ErrorBoundary";
import { DemoGuideButton } from "./DemoGuide";
import { DemoToolsButton } from "./DemoTools";
import { ModeBadge, ModeBanner } from "./ModeBadge";
import { useSimState } from "@/lib/queries";
import { useMode } from "@/lib/live";
import { atLeast, setToken } from "@/lib/auth";
import { useMe } from "@/lib/queries";
import { useEvents } from "@/lib/events";
import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";
const NAV = [
    { to: "/", label: "Today", icon: LayoutDashboard, end: true },
    { to: "/alerts", label: "Alerts", icon: BellRing },
    { to: "/patient", label: "People", icon: User },
    { to: "/falls", label: "Fall Monitor", icon: Activity },
    { to: "/caregiver", label: "Caregiver report", icon: Users },
    { to: "/clinician", label: "Clinician week", icon: Stethoscope, need: "clinician" },
    { to: "/system", label: "System", icon: Settings2 },
];
function NavItems({ row }) {
    const me = useMe();
    return (_jsx(_Fragment, { children: NAV.filter((n) => !n.need || !me.isSuccess || atLeast(me.data?.role, n.need)).map(({ to, label, icon: Icon, end }) => (_jsxs(NavLink, { to: to, end: end, className: ({ isActive }) => cn("flex items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium whitespace-nowrap transition-colors", isActive ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:bg-secondary hover:text-foreground", row && "shrink-0"), children: [_jsx(Icon, { className: "size-4", "aria-hidden": "true" }), label] }, to))) }));
}
export function ThemeToggle() {
    const { theme, toggle } = useTheme();
    return (_jsx(Button, { variant: "outline", size: "icon", onClick: toggle, "aria-label": theme === "dark" ? "Switch to light theme" : "Switch to dark theme", "data-testid": "theme-toggle", children: theme === "dark" ? _jsx(Sun, { className: "size-4", "aria-hidden": "true" }) : _jsx(Moon, { className: "size-4", "aria-hidden": "true" }) }));
}
function TopBar() {
    const sim = useSimState();
    const { conn } = useEvents();
    const { mode, live } = useMode();
    const s = sim.data;
    const e = live.data?.emulator;
    const me = useMe();
    const qc = useQueryClient();
    return (_jsxs("header", { className: "flex flex-wrap items-center gap-x-4 gap-y-2 border-b bg-card px-4 py-2.5", children: [_jsxs("div", { className: "flex items-center gap-2 md:hidden", children: [_jsx(HeartPulse, { className: "size-5 text-primary", "aria-hidden": "true" }), _jsx("span", { className: "font-semibold", children: "ECHOS" })] }), _jsxs("div", { className: "flex min-w-0 flex-1 flex-wrap items-center gap-x-4 gap-y-1 text-sm", "aria-live": "polite", "data-testid": "sim-state", children: [_jsx(ModeBadge, {}), mode === "replay" && _jsxs("span", { className: "font-medium", children: ["Simulation: ", s ? s.status : sim.isError ? "unknown" : "loading"] }), mode === "replay" && s && s.total != null && _jsxs("span", { className: "num hidden text-muted-foreground sm:inline", children: [s.cursor ?? 0, "/", s.total, " events"] }), mode === "replay" && s?.sim_time && _jsx("span", { className: "num hidden text-muted-foreground md:inline", children: s.sim_time.replace("T", " ").slice(0, 16) }), mode === "emulator" && _jsxs("span", { className: "font-medium", children: ["Emulator: ", e ? e.status : "not started"] }), mode === "emulator" && e?.sim_now && _jsxs("span", { className: "num hidden text-muted-foreground md:inline", children: ["data time ", e.sim_now.replace("T", " ").slice(0, 16)] }), mode === "live" && _jsx("span", { className: "font-medium", children: "Receiving from devices" }), s && !s.runtime_alive && _jsx("span", { className: "text-destructive", children: "runtime not running" })] }), _jsxs("span", { className: "inline-flex items-center gap-1.5 text-sm text-muted-foreground", "data-testid": "conn", children: [conn === "open" ? _jsx("span", { className: "size-2 rounded-full bg-normal-line", "aria-hidden": "true" }) : _jsx(WifiOff, { className: "size-4", "aria-hidden": "true" }), _jsx("span", { className: "hidden sm:inline", children: conn === "open" ? "Live" : conn === "reconnecting" ? "Reconnecting…" : "Connecting…" }), _jsx("span", { className: "sr-only sm:hidden", children: conn === "open" ? "Live" : "Offline" })] }), me.data?.auth_enabled && me.data.role && (_jsxs("span", { className: "inline-flex items-center gap-2 text-sm", "data-testid": "role", children: [_jsx("span", { className: "rounded-full border px-2.5 py-0.5 text-xs font-semibold", children: me.data.role }), _jsxs(Button, { variant: "outline", size: "sm", onClick: () => { setToken(null); qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "me" }); qc.invalidateQueries({ queryKey: ["me"] }); }, "data-testid": "logout", children: [_jsx(LogOut, { className: "size-4", "aria-hidden": "true" }), "Log out"] })] })), atLeast(me.data?.role, "operator") && _jsx(DemoToolsButton, {}), mode === "replay" && _jsx(DemoGuideButton, {}), _jsx(ThemeToggle, {})] }));
}
export function ApiBanner() {
    const sim = useSimState();
    if (!sim.isError)
        return null;
    return (_jsxs("div", { role: "alert", "data-testid": "api-banner", className: "flex items-center gap-2 border-b border-urgent-line bg-urgent-bg px-4 py-2 text-sm font-medium text-urgent-fg", children: [_jsx(WifiOff, { className: "size-4 shrink-0", "aria-hidden": "true" }), "API not reachable: start scripts/run_api.sh"] }));
}
export default function Layout() {
    const loc = useLocation();
    return (_jsxs("div", { className: "flex min-h-screen", children: [_jsx("a", { href: "#main", className: "sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-50 focus:rounded-md focus:bg-primary focus:px-3 focus:py-2 focus:text-primary-foreground", children: "Skip to content" }), _jsxs("aside", { className: "sticky top-0 hidden h-screen w-56 shrink-0 flex-col gap-1 border-r bg-card p-3 md:flex", "aria-label": "Main navigation", children: [_jsxs("div", { className: "mb-4 flex items-center gap-2 px-3 py-2", children: [_jsx(HeartPulse, { className: "size-6 text-primary", "aria-hidden": "true" }), _jsxs("div", { className: "leading-tight", children: [_jsx("div", { className: "text-lg font-semibold", children: "ECHOS" }), _jsx("div", { className: "text-xs text-muted-foreground", children: "Decision support, not a diagnosis" })] })] }), _jsx(NavItems, {})] }), _jsxs("div", { className: "flex min-w-0 flex-1 flex-col", children: [_jsx("nav", { className: "flex gap-1 overflow-x-auto border-b bg-card px-2 py-1.5 md:hidden", "aria-label": "Main navigation", children: _jsx(NavItems, { row: true }) }), _jsx(TopBar, {}), _jsx(ApiBanner, {}), _jsx(ModeBanner, {}), _jsx("main", { id: "main", tabIndex: -1, className: "mx-auto w-full max-w-6xl flex-1 p-4 outline-none md:p-6", children: _jsx(ErrorBoundary, { resetKey: loc.pathname, children: _jsx(Outlet, {}) }) })] })] }));
}
