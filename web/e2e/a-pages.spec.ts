import AxeBuilder from "@axe-core/playwright"
import { expect, test } from "@playwright/test"
import { API, json, replayToEnd, setTheme, watchConsole } from "./helpers"

const ROUTES = ["/", "/trace", "/test?tab=audit", "/test?tab=game", "/test?tab=budget", "/review", "/patient", "/alerts", "/agents", "/how"]

test.beforeAll(async () => { await replayToEnd() })

test("each page loads without console errors", async ({ page }) => {
  const errors = watchConsole(page)
  for (const r of ROUTES) {
    await page.goto(r, { waitUntil: "networkidle" })
    await expect(page.locator("main h1").first()).toBeVisible()
  }
  expect(errors.filter((e) => !e.includes("woff2")), errors.join("\n")).toEqual([])
})

test("Morning briefing shows the three claims, the review list with evidence cards, the stats and the caseload", async ({ page }) => {
  const r = await json<{ day: string; items: { patient_id: string; why: string[] }[] }>("/v2/review")
  await page.goto("/")
  await expect(page.getByTestId("review-item")).toHaveCount(r.items.length)
  await expect(page.getByTestId("review-list")).toContainText(r.items[0].patient_id)
  await expect(page.getByTestId("claims")).toContainText("Audit first")
  await expect(page.getByTestId("evidence-card").first()).toBeVisible({ timeout: 20000 })
  await expect(page.getByTestId("stats")).toContainText("Patient-days scored")
  await expect(page.getByTestId("caseload").locator("tbody tr")).toHaveCount((await json<{ items: unknown[] }>("/patients")).items.length)
})

test("Review board moves between days and shows outcomes from the record", async ({ page }) => {
  const r = await json<{ day: string; days: string[] }>("/v2/review")
  await page.goto("/review")
  await expect(page.getByTestId("review-day")).toHaveText(r.day)
  await page.getByRole("button", { name: "Previous day" }).click()
  await expect(page.getByTestId("review-day")).toHaveText(r.days[1])
  await expect(page.getByTestId("outcome").first()).toBeVisible()
})

test("Patient page: belief, charts against the personal usual, day log and the agents' messages", async ({ page }) => {
  const p = (await json<{ items: { patient_id: string }[] }>("/patients")).items[0].patient_id
  await page.goto(`/patients/${p}`)
  await expect(page.getByTestId("belief")).toContainText("95% credible interval")
  for (const t of ["Blood pressure vs this person's usual", "Pulse vs this person's usual", "Agitation probability (highest window of the day)"])
    await expect(page.getByRole("figure", { name: `${t} chart` })).toBeVisible()
  await expect(page.getByTestId("day-log").locator("li").first()).toBeVisible()
  await expect(page.getByTestId("patient-messages")).toContainText("Review Coordinator")
})

test("Under the hood: four agents alive, the crash diff, the message trace and the risks", async ({ page }) => {
  await page.goto("/agents")
  await expect(page.getByTestId("agent-strip").locator("li")).toHaveCount(4)
  await expect(page.getByTestId("agent-strip")).toContainText("alive")
  await expect(page.getByTestId("crash-diff")).toContainText("Lost alerts: 0")
  await expect(page.getByTestId("risks")).toContainText("Circular labels")
  await expect(page.getByTestId("message-trace")).toContainText("review request")
  await page.getByRole("button", { name: "assign review" }).click()
  await expect(page.getByTestId("message-trace").locator("tbody tr").first()).toContainText("assign review")
})

test("Decision trace walks one person-day through eight steps, with the evidence and the memory card", async ({ page }) => {
  await page.goto("/trace")
  for (let n = 1; n <= 8; n++) await expect(page.getByTestId(`trace-step-${n}`)).toBeVisible()
  await expect(page.getByTestId("data-check")).toBeVisible()
  await expect(page.getByTestId("memory-card")).toContainText("numbers")
  await expect(page.getByTestId("memory-card")).toContainText("Models trained per patient: 0")
})

test("Test it yourself: the audit wall, a round of You vs ECHOS, and the budget sliders", async ({ page }) => {
  await page.goto("/test?tab=audit")
  await expect(page.getByTestId("audit")).toContainText("carries a BP label")
  await page.getByTestId("tab-game").click()
  const boxes = page.getByTestId("game-table").getByRole("checkbox")
  for (let i = 0; i < 4; i++) await boxes.nth(i).check()
  await page.getByTestId("reveal").click()
  await expect(page.getByTestId("reveal-result")).toContainText("Best possible")
  await page.getByTestId("tab-budget").click()
  const row = (name: string) => page.getByTestId("budget-table").locator("tr", { hasText: name })
  await expect(row("Random")).toBeVisible()
  await page.getByTestId("k-slider").fill("2")
  await page.getByTestId("fair-select").selectOption("")
  await expect(row("ECHOS")).toContainText("0.45")
})

test("Presenter mode shows the step and the script and moves between screens", async ({ page }) => {
  await page.goto("/")
  await page.getByTestId("presenter-toggle").click()
  await expect(page.getByTestId("presenter-bar")).toContainText("Step 1 of 8")
  await page.getByRole("button", { name: "Next step" }).click()
  await expect(page.getByTestId("presenter-bar")).toContainText("Audit first")
  await expect(page).toHaveURL(/test\?tab=audit/)
})

test("How it works shows the evaluation results and the limitations", async ({ page }) => {
  await page.goto("/how")
  await expect(page.getByTestId("results")).toContainText("Agitation detection")
  await expect(page.getByTestId("results")).toContainText("PR-AUC")
  await expect(page.getByTestId("limitations")).toContainText("Small cohort")
})

test("API-down banner appears when the API is unreachable", async ({ page }) => {
  await page.route(`${API}/**`, (r) => r.abort())
  await page.goto("/")
  await expect(page.getByTestId("api-banner")).toContainText("API not reachable")
})

for (const theme of ["light", "dark"] as const) {
  test(`no serious accessibility violations (${theme})`, async ({ page }) => {
    await setTheme(page, theme)
    for (const r of ROUTES) {
      await page.goto(r, { waitUntil: "networkidle" })
      await page.waitForTimeout(300)
      const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze()
      const bad = res.violations.filter((v) => v.impact === "serious" || v.impact === "critical")
      expect(bad.map((v) => `${r}: ${v.id} ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(", ")}`)).toEqual([])
    }
  })
}
