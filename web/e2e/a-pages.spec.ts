import AxeBuilder from "@axe-core/playwright"
import { expect, test } from "@playwright/test"
import { API, json, replayToEnd, setTheme, watchConsole } from "./helpers"

const ROUTES = ["/", "/review", "/patient", "/alerts", "/agents", "/how"]

test.beforeAll(async () => { await replayToEnd() })

test("each page loads without console errors", async ({ page }) => {
  const errors = watchConsole(page)
  for (const r of ROUTES) {
    await page.goto(r, { waitUntil: "networkidle" })
    await expect(page.locator("main h1").first()).toBeVisible()
  }
  expect(errors.filter((e) => !e.includes("woff2")), errors.join("\n")).toEqual([])
})

test("Today shows the review list with reasons, the stats and the caseload", async ({ page }) => {
  const r = await json<{ day: string; items: { patient_id: string; why: string[] }[] }>("/v2/review")
  await page.goto("/")
  await expect(page.getByTestId("review-item")).toHaveCount(r.items.length)
  await expect(page.getByTestId("review-list")).toContainText(r.items[0].patient_id)
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

test("Agents page: three agents alive and the message trace", async ({ page }) => {
  await page.goto("/agents")
  await expect(page.getByTestId("agent-strip").locator("li")).toHaveCount(3)
  await expect(page.getByTestId("agent-strip")).toContainText("alive")
  await expect(page.getByTestId("message-trace")).toContainText("review request")
  await page.getByRole("button", { name: "assign review" }).click()
  await expect(page.getByTestId("message-trace").locator("tbody tr").first()).toContainText("assign review")
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
