// Screenshots of every page, light + dark, desktop + phone. Starts NOTHING: the API (dry, on the dev copy) and `npm run dev` (web/) must be running.
// Usage: node scripts/ui_screenshots.mjs [baseUrl] [--live]   -> web/screenshots/*.png (--live: file names get a "live-" prefix and the pages are those meaningful with an emulator running)
import { createRequire } from "node:module"
import { mkdirSync } from "node:fs"
import { fileURLToPath } from "node:url"

const require = createRequire(new URL("../web/package.json", import.meta.url))
const { chromium } = require("@playwright/test")
const LIVE = process.argv.includes("--live")
const BASE = process.argv.slice(2).find((a) => a.startsWith("http")) ?? "http://localhost:5173"
const API = process.env.ECHOS_API ?? "http://127.0.0.1:8000/api"
const OUT = fileURLToPath(new URL("../web/screenshots/", import.meta.url))
mkdirSync(OUT, { recursive: true })

const patients = (await (await fetch(`${API}/patients`)).json()).items
const pid = patients.find((p) => p.last_alert_type)?.patient_id ?? patients[0]?.patient_id  // live mode may have no people yet
const withFall = patients.find((p) => p.last_alert_type)
const wk = "2019-W20"
// [name, path, action(page)]
const pages = [
  ["today", "/"],
  ["alerts", "/alerts"],
  ["agents", "/agents"],
  ["how-it-works", "/how-it-works"],
  ["person-case-file", `/patients/${pid}`],
  ["falls", `/falls?patient=${pid}`],
  ["patient-vitals", `/patients/${pid}`],
  ["caregiver", `/caregiver?patient=${withFall?.patient_id ?? pid}&day=${withFall?.last_alert_time?.slice(0, 10) ?? ""}`],
  ["caregiver-how-made", `/caregiver?patient=${withFall?.patient_id ?? pid}`, async (p) => p.getByTestId("provenance").locator("summary").click()],
  ["clinician", `/clinician?patient=6b29b&week=${wk}`, async (p) => p.getByTestId("narrative").waitFor({ timeout: 120000 })],
  ["clinician-source", `/clinician?patient=6b29b&week=${wk}`, async (p) => { await p.getByTestId("references").locator("button").first().click(); await p.getByTestId("source-panel").waitFor() }],
  ["system-health", "/system"],
  ["system-quality", "/system", async (p) => p.getByRole("tab", { name: "Data quality" }).click()],
  ["system-models", "/system", async (p) => p.getByRole("tab", { name: "Models" }).click()],
  ["system-limitations", "/system", async (p) => p.getByRole("tab", { name: "Limitations" }).click()],
  ["demo-guide", "/", async (p) => p.getByTestId("demo-guide-button").click()],
]
const livePages = [
  ["today", "/"],
  ["alerts", "/alerts"],
  ["agents", "/agents"],
  ["how-it-works", "/how-it-works"],
  ["demo-tools", "/", async (p) => p.getByTestId("demo-tools-button").click()],
  ...(pid ? [["person-case-file", `/patients/${pid}`]] : []),
]
const pageList = LIVE ? livePages : pages
const browser = await chromium.launch()
const files = []
for (const [theme, vp] of [["light", [1440, 900]], ["dark", [1440, 900]], ["light", [390, 844]], ["dark", [390, 844]]]) {
  const ctx = await browser.newContext({ viewport: { width: vp[0], height: vp[1] } })
  await ctx.addInitScript((t) => localStorage.setItem("echos-theme-v2", t), theme)
  const page = await ctx.newPage()
  const errors = []
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()))
  page.on("pageerror", (e) => errors.push(String(e)))
  for (const [name, path, act] of pageList) {
    await page.goto(BASE + path, { waitUntil: "networkidle" })
    if (act) await act(page)
    if (name === "falls") await page.waitForSelector('[data-testid="fall-detail"]', { timeout: 15000 }).catch(() => {})
    await page.waitForTimeout(1800)
    const f = `${LIVE ? "live-" : ""}${name}-${theme}-${vp[0]}.png`
    await page.screenshot({ path: OUT + f, fullPage: true })
    files.push(f)
  }
  if (errors.length) console.log(`console errors (${theme} ${vp[0]}):`, [...new Set(errors)].slice(0, 5))
  await ctx.close()
}
await browser.close()
console.log(files.join("\n"))
