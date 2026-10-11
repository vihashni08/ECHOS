import AxeBuilder from "@axe-core/playwright"
import { expect, test } from "@playwright/test"
import { writeFileSync } from "node:fs"
import { API, firstPatient, json, setTheme, watchConsole } from "./helpers"

const ROUTES = ["/", "/falls", "/patient", "/caregiver", "/clinician", "/system"]
const WEEK = "2019-W20"      // contains 201d8's fall on 2019-05-13 (URGENT)

test("each page loads without console errors", async ({ page }) => {
  const errors = watchConsole(page)
  for (const r of ROUTES) {
    await page.goto(r, { waitUntil: "networkidle" })
    await expect(page.locator("main h1").first()).toBeVisible()
  }
  for (const tab of ["Data quality", "Models", "Limitations"]) { await page.getByRole("tab", { name: tab }).click(); await page.waitForTimeout(300) }
  expect(errors, errors.join("\n")).toEqual([])
})

test("theme toggle switches and is remembered", async ({ page }) => {
  await page.goto("/")
  const html = page.locator("html")
  const was = await html.evaluate((e) => e.classList.contains("dark"))
  await page.getByTestId("theme-toggle").click()
  expect(await html.evaluate((e) => e.classList.contains("dark"))).toBe(!was)
  await page.reload()
  expect(await html.evaluate((e) => e.classList.contains("dark"))).toBe(!was)
})

test("API-down banner appears when the API is unreachable", async ({ page }) => {
  await page.route(`${API}/**`, (r) => r.abort())
  await page.goto("/")
  await expect(page.getByTestId("api-banner")).toContainText("API not reachable: start scripts/run_api.sh")
  for (const r of ["/caregiver", "/clinician", "/system"]) { await page.goto(r); await expect(page.getByTestId("api-banner")).toBeVisible(); await expect(page.locator("main h1")).toBeVisible() }
})

test("Caregiver page renders the day's report, the alert time, the 7-day strip and provenance", async ({ page }) => {
  const p = await firstPatient()
  const daily = await json<{ changes: string[]; monitor: string[]; mode: string }>(`/patients/${p.patient_id}/reports/daily?day=${p.latest_day}`)
  await page.goto(`/caregiver?patient=${p.patient_id}&day=${p.latest_day}`)
  const card = page.getByTestId("daily-card")
  await expect(card).toContainText(p.latest_day)
  await expect(card.getByRole("heading", { name: "What changed" })).toBeVisible()
  await expect(card.getByRole("heading", { name: "What to monitor today" })).toBeVisible()
  await expect(card).toContainText(daily.changes[0].slice(0, 30))
  expect(daily.monitor.length).toBeGreaterThanOrEqual(2)
  await expect(page.getByTestId("week-strip").locator("li")).toHaveCount(7)
  await expect(page.getByTestId("daily-card").locator("text=/probab/i")).toHaveCount(0)
  const prov = page.getByTestId("provenance")
  await prov.locator("summary").click()
  await expect(prov).toContainText("Fixed template")
  // a day with a fall shows the alert and its time at the top
  await page.goto(`/caregiver?patient=201d8&day=2019-05-13`)
  await expect(page.getByTestId("alert-banner")).toContainText("Possible fall at 10:18")
})

test("Clinician page: report data, citation side panel, PDF links, dry-run send with second confirmation", async ({ page }) => {
  await page.goto(`/clinician?patient=201d8&week=${WEEK}`)
  await expect(page.getByTestId("narrative")).toBeVisible({ timeout: 100000 })        // first visit builds the weekly report (nothing is emailed)
  const w = await json<{ data: { status_counts: Record<string, number>; falls: unknown[] }; narrative: string }>(`/patients/201d8/reports/weekly?week=${WEEK}`)
  await expect(page.getByTestId("tiles")).toContainText(`Urgent ${w.data.status_counts.URGENT}`)
  await expect(page.getByTestId("narrative")).toContainText(w.narrative.slice(0, 40))
  await expect(page.getByTestId("alerts-table")).toContainText("Possible fall")
  await expect(page.getByTestId("dq-line")).toContainText("Data quality")
  const refs = page.getByTestId("references").locator("button")
  expect(await refs.count()).toBeGreaterThan(0)
  const label = await refs.first().getAttribute("aria-label")
  await refs.first().click()
  const panel = page.getByTestId("source-panel")
  await expect(panel).toBeVisible()
  await expect(panel).toContainText("Title")
  await expect(panel).toContainText("Excerpt")
  expect(label).toContain("Open source")
  await page.keyboard.press("Escape")
  await expect(panel).toBeHidden()
  await expect(page.getByTestId("download-pdf")).toHaveAttribute("href", /reports\/weekly\/pdf\?week=2019-W20/)
  await expect(page.getByTestId("preview-pdf")).toHaveAttribute("target", "_blank")
  const pdf = await page.request.get((await page.getByTestId("download-pdf").getAttribute("href"))!)
  expect(pdf.headers()["content-type"]).toBe("application/pdf")
  // dry-run send
  const weeklyRows = () => page.getByTestId("outbox-list").locator("li", { hasText: "201d8_2019-W20.pdf" })
  await page.getByTestId("send-weekly").click()
  await expect(page.getByRole("alertdialog")).toContainText("DRY RUN")
  await page.getByTestId("confirm-send").click()
  await expect(page.getByTestId("send-result")).toContainText("SENT", { timeout: 60000 })
  await expect(page.getByTestId("send-result")).toContainText("dry run")
  await expect(weeklyRows()).toHaveCount(1)
  await expect(weeklyRows().first()).toContainText("Dry run")
  // second send: asks again before sending another copy
  await page.getByTestId("send-weekly").click()
  await page.getByTestId("confirm-send").click()
  await expect(page.getByRole("alertdialog")).toContainText("already sent")
  await page.getByRole("button", { name: "Keep one copy" }).click()
  await expect(weeklyRows()).toHaveCount(1)
})

test("System tabs render and the numbers equal /system/metrics", async ({ page }) => {
  const m = await json<{ files: Record<string, any> }>("/system/metrics")        // eslint-disable-line @typescript-eslint/no-explicit-any
  const ms = m.files.model_selection, cal = m.files.calibration, audit = m.files.fall_audit
  await page.goto("/system")
  await expect(page.getByRole("table", { name: "Agent heartbeats" })).toBeVisible()
  await page.getByRole("tab", { name: "Data quality" }).click()
  await expect(page.getByTestId("quality")).toContainText("Patient-days processed")
  await page.getByRole("tab", { name: "Models" }).click()
  const [name, r] = Object.entries(ms.vitals.models)[0] as [string, any]       // eslint-disable-line @typescript-eslint/no-explicit-any
  await expect(page.getByTestId(`vitals-${name}-prauc`)).toHaveText(`${r.pr_auc.value.toFixed(3)} (${r.pr_auc.ci95.map((x: number) => x.toFixed(3)).join("–")})`)
  await expect(page.getByTestId("vitals-brier-raw")).toHaveText(cal.vitals.brier_raw.toFixed(3))
  const t = audit.thresholds[3]
  await expect(page.getByTestId(`curve-${t}`)).toHaveText(audit.curve.find((c: any) => c.threshold === t && !c.confirm_inactivity).adult_sensitivity.toFixed(3))      // eslint-disable-line @typescript-eslint/no-explicit-any
  const e = ms.fall_elderly_by_threshold.rows[0]
  await expect(page.getByTestId(`elder-${e.threshold}-no`)).toContainText(e.without_inactivity.sensitivity.toFixed(3))
  await page.getByRole("tab", { name: "Limitations" }).click()
  await expect(page.getByTestId("limitations")).not.toBeEmpty()
  await expect(page.getByText("not lost")).toBeVisible()
})

test("keyboard: demo guide drawer traps focus, closes with Escape and returns focus", async ({ page }) => {
  await page.goto("/", { waitUntil: "networkidle" })
  const btn = page.getByTestId("demo-guide-button")
  await expect(btn).toBeVisible()
  await btn.focus()
  await page.keyboard.press("Enter")
  const dlg = page.getByTestId("demo-guide")
  await expect(dlg).toBeVisible()
  for (let i = 0; i < 14; i++) {
    await page.keyboard.press("Tab")
    expect(await page.evaluate(() => !!document.activeElement?.closest('[role="dialog"]'))).toBe(true)      // focus never leaves the drawer
  }
  await page.keyboard.press("Escape")
  await expect(dlg).toBeHidden()
  await expect(btn).toBeFocused()
})

test("axe: no serious or critical violations on any page, light and dark", async ({ page }) => {
  const p = await firstPatient()
  const pages = ["/", "/falls", `/patients/${p.patient_id}`, `/caregiver?patient=${p.patient_id}&day=${p.latest_day}`, `/clinician?patient=201d8&week=${WEEK}`, "/system"]
  const found: { page: string; theme: string; id: string; impact: string; nodes: number; first: string }[] = []
  for (const theme of ["light", "dark"] as const) {
    await setTheme(page, theme)
    for (const path of pages) {
      await page.goto(path, { waitUntil: "networkidle" })
      await page.waitForTimeout(1500)
      if (path === "/system") for (const tab of ["Data quality", "Models", "Limitations"]) {
        await page.getByRole("tab", { name: tab }).click()
        const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze()
        r.violations.forEach((v) => found.push({ page: `${path}#${tab}`, theme, id: v.id, impact: v.impact ?? "n/a", nodes: v.nodes.length, first: `${v.nodes[0].target.join(" ")} :: ${(v.nodes[0].any[0]?.message ?? v.nodes[0].failureSummary ?? "").slice(0, 160)}` }))
      }
      const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze()
      r.violations.forEach((v) => found.push({ page: path, theme, id: v.id, impact: v.impact ?? "n/a", nodes: v.nodes.length, first: `${v.nodes[0].target.join(" ")} :: ${(v.nodes[0].any[0]?.message ?? v.nodes[0].failureSummary ?? "").slice(0, 160)}` }))
    }
  }
  const bySeverity = found.reduce<Record<string, number>>((a, v) => ({ ...a, [v.impact]: (a[v.impact] ?? 0) + 1 }), {})
  writeFileSync("test-results-axe.json", JSON.stringify({ bySeverity, found }, null, 1))
  console.log("axe violations by severity:", JSON.stringify(bySeverity))
  expect(found.filter((v) => v.impact === "serious" || v.impact === "critical"), JSON.stringify(found, null, 1)).toEqual([])
})
