import { useEffect, useState } from "react"
import { useLive } from "./queries"

export type Mode = "replay" | "emulator" | "live"
export const MODE: Record<Mode, { label: string; short: string; cls: string; what: string }> = {
  live: { label: "LIVE", short: "Live", cls: "bg-normal-bg text-normal-fg border-normal-line", what: "Events arrive from real devices through the ingestion API." },
  emulator: { label: "EMULATOR", short: "Emulator", cls: "bg-monitor-bg text-monitor-fg border-monitor-line", what: "A labelled device emulator replays real TIHM vitals readings in wall-clock time. It is not a real device; falls are injected from SisFall." },
  replay: { label: "REPLAY", short: "Replay", cls: "bg-gap-bg text-gap-fg border-gap-line", what: "The TIHM study replayed day by day, for evaluation and demos. This is not live data." },
}

export function useMode(): { mode: Mode; live: ReturnType<typeof useLive> } {
  const live = useLive()
  return { mode: ((live.data?.mode as Mode) ?? "replay"), live }
}

/** Re-render every `ms` (countdowns). */
export function useNow(ms = 1000): number {
  const [t, setT] = useState(() => Date.now() / 1000)
  useEffect(() => { const i = setInterval(() => setT(Date.now() / 1000), ms); return () => clearInterval(i) }, [ms])
  return t
}

export const mmss = (s: number) => { const x = Math.max(0, Math.round(s)); return `${Math.floor(x / 60)}:${String(x % 60).padStart(2, "0")}` }
export const ago = (s?: number | null) => (s == null ? "never" : s < 5 ? "just now" : s < 90 ? `${Math.round(s)} s ago` : s < 5400 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`)
export const personLabel = (id: string) => `Patient ${id}`
