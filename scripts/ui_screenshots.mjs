// Screenshots of every page (Morning briefing, Decision trace, Test it yourself x3, Review board, Patient, Alerts, Under the hood, How it works, Demo tools), light + dark, desktop + phone. Starts NOTHING: scripts/start.sh must be running (play the replay first).
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
const pid0 = patients.find((p) => p.last_alert_type)?.patient_id ?? patients[0]?.patient_id
// An informative day for the briefing and the trace: many evidence cards written by Gemini, personal-baseline or urgent reasons, and no cohort-wide sensor outage.
const reviewDays = (await (await fetch(`${API}/v2/review`)).json()).days
let best = { score: -1, day: reviewDays[0], pid: pid0 }
for (const d of reviewDays) {
  const r = await (await fetch(`${API}/v2/review?day=${d}`)).json()
  const cards = await (await fetch(`${API}/v2/evidence?day=${d}`)).json()
  const silent = r.items.filter((i) => i.why.some((w) => w.startsWith("data check:"))).length
  if (silent * 2 >= r.items.length && silent >= 2) continue
  const good = r.items.filter((i) => i.why.some((w) => /URGENT|vs usual|agitation risk/.test(w)))
  const gem = cards.filter((c) => c.source === "gemini" && c.kind === "review").length
  const score = gem + 2 * good.length
  if (score > best.score) best = { score, day: d, pid: (good[0] ?? r.items[0])?.patient_id ?? pid0 }
}
console.log(`briefing / trace day: ${best.day}, person ${best.pid}`)
const pid = best.pid
// [name, path, action(page)]
const pickFour = async (p) => {                                   // You vs ECHOS: pick four people and reveal what happened
  const boxes = p.getByTestId("game-table").getByRole("checkbox")
  for (let i = 0; i < 4; i++) await boxes.nth(i).check()
  await p.getByTestId("reveal").click()
}
const pages = [
  ["briefing", `/?day=${best.day}`],
  ["decision-trace", `/trace/${best.pid}/${best.day}`],
  ["test-audit", "/test?tab=audit"],
  ["test-game", "/test?tab=game", pickFour],
  ["test-budget", "/test?tab=budget"],
  ["review-board", "/review"],
  ["patient", `/patients/${pid}`],
  ["alerts", "/alerts"],
  ["under-the-hood", "/agents"],
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
