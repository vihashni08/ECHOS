import AxeBuilder from "@axe-core/playwright"
import { expect, test } from "@playwright/test"

const API = "http://127.0.0.1:8021/api"

// One story in order (the stack is fresh and the tests share it): empty -> start the emulator -> data arrives -> inject a fall -> acknowledge -> case file.
test.describe.configure({ mode: "serial" })

test("empty state explains what to do and the mode is labelled EMULATOR", async ({ page }) => {
  await page.goto("/", { waitUntil: "networkidle" })
  await expect(page.getByTestId("mode-badge")).toHaveText("EMULATOR")
  await expect(page.getByTestId("mode-banner")).toContainText("not a real device")
  await expect(page.getByText("No data yet")).toBeVisible()
  await expect(page.getByTestId("demo-guide-button")).toHaveCount(0)            // the replay guide is not offered in emulator mode
})

test("starting the emulator makes people appear with a status and fresh data", async ({ page, request }) => {
  await page.goto("/", { waitUntil: "networkidle" })
  await page.getByTestId("demo-tools-button").click()
  await expect(page.getByTestId("emulator-controls")).toContainText("not a device")
  await page.getByTestId("emu-play").click()
  await expect(page.getByTestId("emu-status")).toContainText("playing", { timeout: 20000 })
  await page.keyboard.press("Escape")
  await expect(page.getByTestId("today-summary")).toContainText(/\d+ (person needs|people need) attention/, { timeout: 60000 })
  await expect(page.getByTestId("fine-list").or(page.getByTestId("attention-list")).first()).toBeVisible({ timeout: 60000 })
  await expect(page.getByTestId("freshness")).toContainText("last event", { timeout: 30000 })
  const st = await (await request.get(`${API}/live/status`)).json()
  expect(st.mode).toBe("emulator")
  expect(st.patients.length).toBeGreaterThan(0)
})

test("an injected fall raises one alert that can be acknowledged and resolved; the case file explains it", async ({ page, request }) => {
  const st = await (await request.get(`${API}/live/status`)).json()
  const pid = st.emulator.patients[0]
  // pin a fall type the model reliably detects with confirmed post-fall inactivity (as the replay e2e does)
  await page.route(`${API}/live/emulator`, async (route) => {
    const b = route.request().postDataJSON()
    await route.continue({ postData: JSON.stringify(b.action === "fall" ? { ...b, activity: "F02", subject: "SA03" } : b) })
  })
  await page.goto("/", { waitUntil: "networkidle" })
  await page.getByTestId("demo-tools-button").click()
  await page.getByRole("combobox", { name: "Patient for the fall" }).click()
  await page.getByRole("option", { name: `Patient ${pid}` }).click()
  await page.getByTestId("emu-fall").click()
  await expect(page.getByTestId("emu-msg")).toContainText("SYNTHETIC", { timeout: 20000 })
  await page.keyboard.press("Escape")
  const card = page.getByTestId("open-alerts").getByTestId("alert-card").first()
  await expect(card).toContainText("Fall alert", { timeout: 60000 })
  await expect(card.getByTestId("alert-state")).toHaveText("SENT", { timeout: 30000 })
  await expect(card.getByTestId("countdown")).toBeVisible()
  await card.getByTestId("ack").click()
  await expect(page.getByTestId("open-alerts")).toHaveCount(0, { timeout: 15000 })       // acknowledged: no longer "needs action now"
  await page.getByRole("link", { name: "Alerts" }).first().click()
  await page.getByTestId("tab-all").click()
  const row = page.getByTestId("alert-list").getByTestId("alert-card").first()
  await expect(row.getByTestId("alert-state")).toHaveText("ACKNOWLEDGED")
  await expect(page.getByText("Confirm the person is well after the fall")).toBeVisible({ timeout: 15000 })      // the Care Coordinator opened a check-in
  await row.getByTestId("resolve").click()
  await expect(row.getByTestId("alert-state")).toHaveText("RESOLVED")
  const alerts = await (await request.get(`${API}/alerts`)).json()
  expect(alerts.items.filter((a: { type: string }) => a.type === "fall")).toHaveLength(1)        // exactly one alert for one fall
  await page.goto(`/patients/${pid}`, { waitUntil: "networkidle" })
  await expect(page.getByTestId("case-headline")).toContainText("fall")
  await expect(page.getByTestId("evidence-level")).toContainText("evidence")
  await page.getByTestId("case-trace").locator("summary").click()
  await expect(page.getByTestId("case-trace")).toContainText("Read what the detectors found")
})

test("How it works lists three detectors and four agents, with the message bus", async ({ page }) => {
  await page.goto("/how-it-works", { waitUntil: "networkidle" })
  await expect(page.locator('[data-kind="detector"]')).toHaveCount(3)
  await expect(page.locator('[data-kind="agent"]')).toHaveCount(4)
  await expect(page.getByTestId("component-coordinator")).toContainText("Care Coordinator")
  await expect(page.getByTestId("component-coordinator").getByTestId("component-status")).toContainText("running")
  await expect(page.getByTestId("bus")).toContainText("case_opened")
})

test("axe: no serious or critical violations on the live-mode pages", async ({ page }) => {
  const found: unknown[] = []
  for (const path of ["/", "/alerts", "/how-it-works"]) {
    await page.goto(path, { waitUntil: "networkidle" })
    await page.waitForTimeout(1000)
    const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze()
    found.push(...r.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => ({ path, id: v.id, first: v.nodes[0].target.join(" ") })))
  }
  expect(found, JSON.stringify(found, null, 1)).toEqual([])
})
