import { useQueryClient } from "@tanstack/react-query"
import { Activity, BellRing, LogOut, HeartPulse, LayoutDashboard, Moon, Settings2, Stethoscope, Sun, User, Users, WifiOff } from "lucide-react"
import { NavLink, Outlet, useLocation } from "react-router-dom"
import { Button } from "@/components/ui/button"
import ErrorBoundary from "./ErrorBoundary"
import { DemoGuideButton } from "./DemoGuide"
import { DemoToolsButton } from "./DemoTools"
import { ModeBadge, ModeBanner } from "./ModeBadge"
import { useSimState } from "@/lib/queries"
import { useMode } from "@/lib/live"
import { atLeast, setToken, type Role } from "@/lib/auth"
import { useMe } from "@/lib/queries"
import { useEvents } from "@/lib/events"
import { useTheme } from "@/lib/theme"
import { cn } from "@/lib/utils"

const NAV: { to: string; label: string; icon: typeof User; end?: boolean; need?: Role }[] = [
  { to: "/", label: "Today", icon: LayoutDashboard, end: true },
  { to: "/alerts", label: "Alerts", icon: BellRing },
  { to: "/patient", label: "Patient", icon: User },
  { to: "/caregiver", label: "Daily report", icon: Users },
  { to: "/clinician", label: "Weekly report", icon: Stethoscope, need: "clinician" },
  { to: "/falls", label: "Fall details", icon: Activity },
  { to: "/system", label: "How it works", icon: Settings2 },
]

function NavItems({ row }: { row?: boolean }) {
  const me = useMe()
  return (
    <>
      {NAV.filter((n) => !n.need || !me.isSuccess || atLeast(me.data?.role, n.need)).map(({ to, label, icon: Icon, end }) => (
        <NavLink key={to} to={to} end={end}
          className={({ isActive }) => cn("flex items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium whitespace-nowrap transition-colors",
            isActive ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:bg-secondary hover:text-foreground", row && "shrink-0")}>
          <Icon className="size-4" aria-hidden="true" />
          {label}
        </NavLink>
      ))}
    </>
  )
}

export function ThemeToggle() {
  const { theme, toggle } = useTheme()
  return (
    <Button variant="outline" size="icon" onClick={toggle} aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"} data-testid="theme-toggle">
      {theme === "dark" ? <Sun className="size-4" aria-hidden="true" /> : <Moon className="size-4" aria-hidden="true" />}
    </Button>
  )
}

function TopBar() {
  const sim = useSimState()
  const { conn } = useEvents()
  const { mode, live } = useMode()
  const s = sim.data
  const e = live.data?.emulator
  const me = useMe()
  const qc = useQueryClient()
  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b bg-card px-4 py-2.5">
      <div className="flex items-center gap-2 md:hidden">
        <HeartPulse className="size-5 text-primary" aria-hidden="true" />
        <span className="font-semibold">ECHOS</span>
      </div>
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-4 gap-y-1 text-sm" aria-live="polite" data-testid="sim-state">
        <ModeBadge />
        {mode === "replay" && <span className="font-medium">Simulation: {s ? s.status : sim.isError ? "unknown" : "loading"}</span>}
        {mode === "replay" && s && s.total != null && <span className="num hidden text-muted-foreground sm:inline">{s.cursor ?? 0}/{s.total} events</span>}
        {mode === "replay" && s?.sim_time && <span className="num hidden text-muted-foreground md:inline">{s.sim_time.replace("T", " ").slice(0, 16)}</span>}
        {mode === "emulator" && <span className="font-medium">Emulator: {e ? e.status : "not started"}</span>}
        {mode === "emulator" && e?.sim_now && <span className="num hidden text-muted-foreground md:inline">data time {e.sim_now.replace("T", " ").slice(0, 16)}</span>}
        {mode === "live" && <span className="font-medium">Receiving from devices</span>}
        {s && !s.runtime_alive && <span className="text-destructive">runtime not running</span>}
      </div>
      <span className="inline-flex items-center gap-1.5 text-sm text-muted-foreground" data-testid="conn">
        {conn === "open" ? <span className="size-2 rounded-full bg-normal-line" aria-hidden="true" /> : <WifiOff className="size-4" aria-hidden="true" />}
        <span className="hidden sm:inline">{conn === "open" ? "Live" : conn === "reconnecting" ? "Reconnecting…" : "Connecting…"}</span><span className="sr-only sm:hidden">{conn === "open" ? "Live" : "Offline"}</span>
      </span>
      {me.data?.auth_enabled && me.data.role && (
        <span className="inline-flex items-center gap-2 text-sm" data-testid="role"><span className="rounded-full border px-2.5 py-0.5 text-xs font-semibold">{me.data.role}</span>
          <Button variant="outline" size="sm" onClick={() => { setToken(null); qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "me" }); qc.invalidateQueries({ queryKey: ["me"] }) }} data-testid="logout"><LogOut className="size-4" aria-hidden="true" />Log out</Button></span>)}
      {atLeast(me.data?.role, "operator") && <DemoToolsButton />}
      {mode === "replay" && <DemoGuideButton />}
      <ThemeToggle />
    </header>
  )
}

export function ApiBanner() {
  const sim = useSimState()
  if (!sim.isError) return null
  return (
    <div role="alert" data-testid="api-banner" className="flex items-center gap-2 border-b border-urgent-line bg-urgent-bg px-4 py-2 text-sm font-medium text-urgent-fg">
      <WifiOff className="size-4 shrink-0" aria-hidden="true" />
      API not reachable: start scripts/run_api.sh
    </div>
  )
}

export default function Layout() {
  const loc = useLocation()
  return (
    <div className="flex min-h-screen">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-50 focus:rounded-md focus:bg-primary focus:px-3 focus:py-2 focus:text-primary-foreground">Skip to content</a>
      <aside className="sticky top-0 hidden h-screen w-56 shrink-0 flex-col gap-1 border-r bg-card p-3 md:flex" aria-label="Main navigation">
        <div className="mb-4 flex items-center gap-2 px-3 py-2">
          <HeartPulse className="size-6 text-primary" aria-hidden="true" />
          <div className="leading-tight"><div className="text-lg font-semibold">ECHOS</div><div className="text-xs text-muted-foreground">Decision support, not a diagnosis</div></div>
        </div>
        <NavItems />
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <nav className="flex gap-1 overflow-x-auto border-b bg-card px-2 py-1.5 md:hidden" aria-label="Main navigation"><NavItems row /></nav>
        <TopBar />
        <ApiBanner />
        <ModeBanner />
        <main id="main" tabIndex={-1} className="mx-auto w-full max-w-6xl flex-1 p-4 outline-none md:p-6"><ErrorBoundary resetKey={loc.pathname}><Outlet /></ErrorBoundary></main>
      </div>
    </div>
  )
}
