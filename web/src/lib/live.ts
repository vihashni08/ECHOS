import { useEffect, useState } from "react"

/** Re-render every `ms` (countdowns). */
export function useNow(ms = 1000): number {
  const [t, setT] = useState(() => Date.now() / 1000)
  useEffect(() => { const i = setInterval(() => setT(Date.now() / 1000), ms); return () => clearInterval(i) }, [ms])
  return t
}

export const mmss = (s: number) => { const x = Math.max(0, Math.round(s)); return `${Math.floor(x / 60)}:${String(x % 60).padStart(2, "0")}` }
export const ago = (s?: number | null) => (s == null ? "never" : s < 5 ? "just now" : s < 90 ? `${Math.round(s)} s ago` : s < 5400 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`)
export const personLabel = (id: string) => `Patient ${id}`
export const pct = (v?: number | null, d = 0) => (v == null ? "n/a" : `${(v * 100).toFixed(d)}%`)
