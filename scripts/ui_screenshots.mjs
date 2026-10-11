// Screenshots of every page, light + dark, desktop + phone. Starts NOTHING: the API (dry, on the dev copy) and `npm run dev` (web/) must be running.
// Usage: node scripts/ui_screenshots.mjs [baseUrl]   -> web/screenshots/*.png
import { createRequire } from "node:module"
import { mkdirSync } from "node:fs"
import { fileURLToPath } from "node:url"

const require = createRequire(new URL("../web/package.json", import.meta.url))
const { chromium } = require("@playwright/test")
const BASE = process.argv[2] ?? "http://localhost:5173"
const API = "http://127.0.0.1:8000/api"
const OUT = fileURLToPath(new URL("../web/screenshots/", import.meta.url))
mkdirSync(OUT, { recursive: true })

const patients = (await (await fetch(`${API}/patients`)).json()).items
const pid = patients.find((p) => p.last_alert_type)?.patient_id ?? patients[0]?.patient_id
const pages = [
  ["dashboard", "/"],
  ["falls", `/falls?patient=${pid}`],
  ["patient-vitals", `/patients/${pid}`],
  ["patient-routine", `/patients/${pid}`, "routine"],
  ["caregiver", "/caregiver"],
]
const browser = await chromium.launch()
const files = []
for (const [theme, vp] of [["light", [1440, 900]], ["dark", [1440, 900]], ["light", [390, 844]], ["dark", [390, 844]]]) {
  const ctx = await browser.newContext({ viewport: { width: vp[0], height: vp[1] } })
  await ctx.addInitScript((t) => localStorage.setItem("echos-theme", t), theme)
  const page = await ctx.newPage()
  const errors = []
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()))
  page.on("pageerror", (e) => errors.push(String(e)))
  for (const [name, path, tab] of pages) {
    await page.goto(BASE + path, { waitUntil: "networkidle" })
    if (tab) await page.getByRole("tab", { name: new RegExp(tab, "i") }).click()
    if (name === "falls") await page.waitForSelector('[data-testid="fall-detail"]', { timeout: 15000 }).catch(() => {})
    await page.waitForTimeout(1800)
    const f = `${name}-${theme}-${vp[0]}.png`
    await page.screenshot({ path: OUT + f, fullPage: true })
    files.push(f)
  }
  if (errors.length) console.log(`console errors (${theme} ${vp[0]}):`, [...new Set(errors)].slice(0, 5))
  await ctx.close()
}
await browser.close()
console.log(files.join("\n"))
