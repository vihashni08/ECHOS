import type { Page } from "@playwright/test"

export const API = process.env.API_URL ?? "http://127.0.0.1:8010/api"

export function watchConsole(page: Page) {
  const errors: string[] = []
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()))
  page.on("pageerror", (e) => errors.push(String(e)))
  page.on("response", (r) => r.status() >= 400 && errors.push(`HTTP ${r.status()} ${r.url()}`))
  return errors
}
export async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${API}${path}`, init)
  if (!r.ok) throw new Error(`${path}: ${r.status}`)
  return (await r.json()) as T
}
export const post = <T,>(path: string, body: unknown) => json<T>(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) })

/** Play the replay to the end (10 demo days) once; later calls return at once. */
export async function replayToEnd(): Promise<void> {
  const s = await json<{ status: string }>("/sim/state")
  if (s.status === "idle") { await post("/sim/control", { action: "speed", speed: 200 }); await post("/sim/control", { action: "play" }) }
  for (let i = 0; i < 240; i++) {
    const st = await json<{ status: string; total: number; cursor: number }>("/sim/state")
    const r = await json<{ days: string[] }>("/v2/review")
    if (st.status === "finished" && r.days.length >= 10) return
    await new Promise((res) => setTimeout(res, 500))
  }
  throw new Error("replay did not finish")
}
export const setTheme = (page: Page, t: "light" | "dark") => page.addInitScript((v) => localStorage.setItem("echos-theme-v2", v), t)
