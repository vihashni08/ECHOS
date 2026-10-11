export const fmtTime = (iso?: string | null) => (iso ? iso.replace("T", " ").slice(0, 16) : "n/a")
export const fmtDay = (iso?: string | null) => (iso ? iso.slice(0, 10) : "n/a")
export const ageHuman = (s?: number | null) => (s == null ? "n/a" : s < 90 ? `${Math.round(s)} s` : `${Math.round(s / 60)} min`)
export const channelLabel = (c?: string | null) => (c === "dryrun" ? "Dry run (nothing emailed)" : c === "smtp" ? "Email (SMTP)" : c ?? "n/a")
export const typeLabel = (t?: string | null) => (t === "fall" ? "Fall alert" : t === "critical_vitals" ? "Critical vitals" : t ?? "none")
export function isoWeek(day: string): string {
  const d = new Date(day + "T00:00:00Z")
  const dow = (d.getUTCDay() + 6) % 7
  d.setUTCDate(d.getUTCDate() - dow + 3)
  const y = d.getUTCFullYear()
  const wk = 1 + Math.round(((d.getTime() - Date.UTC(y, 0, 4)) / 86400000 - 3 + ((new Date(Date.UTC(y, 0, 4)).getUTCDay() + 6) % 7)) / 7)
  return `${y}-W${String(wk).padStart(2, "0")}`
}
export const fmtEpoch = (t?: number | null) => (t ? new Date(t * 1000).toISOString().slice(5, 16).replace("T", " ") : "n/a")
export const fmt3 = (v: unknown) => (typeof v === "number" ? v.toFixed(3) : "n/a")
export const fmtCI = (o?: { value?: number; ci95?: number[] } | null) =>
  o && typeof o.value === "number" ? `${o.value.toFixed(3)} (${(o.ci95 ?? []).map((x) => x.toFixed(3)).join("–")})` : "n/a"
