import type { Page } from "@playwright/test"

export const API = process.env.API_URL ?? "http://127.0.0.1:8010/api"

export function watchConsole(page: Page) {
  const errors: string[] = []
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()))
  page.on("pageerror", (e) => errors.push(String(e)))
  page.on("response", (r) => r.status() >= 400 && errors.push(`HTTP ${r.status()} ${r.url()}`))
  return errors
}
export async function json<T>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`)
  if (!r.ok) throw new Error(`${path}: ${r.status}`)
  return (await r.json()) as T
}
export async function firstPatient(): Promise<{ patient_id: string; latest_day: string }> {
  const p = await json<{ items: { patient_id: string; latest_day: string; last_alert_type: string | null }[] }>("/patients")
  return p.items.find((x) => x.last_alert_type) ?? p.items[0]
}
export const setTheme = (page: Page, t: "light" | "dark") => page.addInitScript((v) => localStorage.setItem("echos-theme-v2", v), t)
