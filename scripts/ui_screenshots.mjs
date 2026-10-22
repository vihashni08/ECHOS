// Screenshots of every page, light + dark, desktop + phone. Starts NOTHING: scripts/start.sh must be running (play the replay first).
// Usage: node scripts/ui_screenshots.mjs [baseUrl]   -> web/screenshots/*.png
import { createRequire } from "node:module"
import { mkdirSync } from "node:fs"
import { fileURLToPath } from "node:url"

const require = createRequire(new URL("../web/package.json", import.meta.url))
const { chromium } = require("@playwright/test")
const BASE = process.argv.slice(2).find((a) => a.startsWith("http")) ?? "http://localhost:5173"
const API = process.env.ECHOS_API ?? "http://127.0.0.1:8000/api"
const OUT = fileURLToPath(new URL("../web/screenshots/", import.meta.url))
mkdirSync(OUT, { recursive: true })

const patients = (await (await fetch(`${API}/patients`)).json()).items
const pid = patients.find((p) => p.last_alert_type)?.patient_id ?? patients[0]?.patient_id
// [name, path, action(page)]
const pages = [
  ["today", "/"],
  ["review-board", "/review"],
  ["patient", `/patients/${pid}`],
  ["alerts", "/alerts"],
  ["agents", "/agents"],
  ["how-it-works", "/how"],
  ["demo-tools", "/", async (p) => p.getByTestId("demo-tools-button").click()],
]
const pageList = pages
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
