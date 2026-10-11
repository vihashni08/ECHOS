import { Activity, HeartPulse, LayoutDashboard, Moon, Radio, Settings2, Stethoscope, Sun, User, Users, WifiOff } from "lucide-react"
import { NavLink, Outlet } from "react-router-dom"
import { Button } from "@/components/ui/button"
import { useSimState } from "@/lib/queries"
import { useEvents } from "@/lib/events"
import { useTheme } from "@/lib/theme"
import { cn } from "@/lib/utils"

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/falls", label: "Fall Monitor", icon: Activity },
  { to: "/patient", label: "Patient", icon: User },
  { to: "/caregiver", label: "Caregiver", icon: Users },
  { to: "/clinician", label: "Clinician", icon: Stethoscope },
  { to: "/system", label: "System", icon: Settings2 },
]

function NavItems({ row }: { row?: boolean }) {
  return (
    <>
      {NAV.map(({ to, label, icon: Icon, end }) => (
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
  const s = sim.data
  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b bg-card px-4 py-2.5">
      <div className="flex items-center gap-2 md:hidden">
        <HeartPulse className="size-5 text-primary" aria-hidden="true" />
        <span className="font-semibold">ECHOS</span>
      </div>
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-4 gap-y-1 text-sm" aria-live="polite" data-testid="sim-state">
        <span className="inline-flex items-center gap-1.5 font-medium">
          <Radio className="size-4 text-muted-foreground" aria-hidden="true" />
          Simulation: {s ? s.status : sim.isError ? "unknown" : "loading"}
        </span>
        {s && s.total != null && <span className="num text-muted-foreground">{s.cursor ?? 0}/{s.total} events</span>}
        {s?.sim_time && <span className="num text-muted-foreground">{s.sim_time.replace("T", " ").slice(0, 16)}</span>}
        {s && s.speed != null && <span className="num text-muted-foreground">{s.speed} ev/s</span>}
        {s && !s.runtime_alive && <span className="text-destructive">runtime not running</span>}
      </div>
      <span className="inline-flex items-center gap-1.5 text-sm text-muted-foreground" data-testid="conn">
        {conn === "open" ? <span className="size-2 rounded-full bg-normal-line" aria-hidden="true" /> : <WifiOff className="size-4" aria-hidden="true" />}
        {conn === "open" ? "Live" : conn === "reconnecting" ? "Reconnecting…" : "Connecting…"}
      </span>
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
  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-56 shrink-0 flex-col gap-1 border-r bg-card p-3 md:flex" aria-label="Main navigation">
        <div className="mb-4 flex items-center gap-2 px-3 py-2">
          <HeartPulse className="size-6 text-primary" aria-hidden="true" />
          <div className="leading-tight"><div className="text-lg font-semibold">ECHOS</div><div className="text-xs text-muted-foreground">Decision support, not diagnosis</div></div>
        </div>
        <NavItems />
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <nav className="flex gap-1 overflow-x-auto border-b bg-card px-2 py-1.5 md:hidden" aria-label="Main navigation"><NavItems row /></nav>
        <TopBar />
        <ApiBanner />
        <main className="mx-auto w-full max-w-6xl flex-1 p-4 md:p-6"><Outlet /></main>
      </div>
    </div>
  )
}
