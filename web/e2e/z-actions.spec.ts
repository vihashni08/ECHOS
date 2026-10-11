import { expect, test } from "@playwright/test"
import { API } from "./helpers"

// These tests RESET the throw-away e2e database (never the UI dev copy) and inject falls; they run after the read-only specs.
test("Simulate fall raises an URGENT alert in the feed and shows the Alert sent panel with two recipients", async ({ page, request }) => {
  await request.post(`${API}/sim/control`, { data: { action: "reset" } })
  await request.post(`${API}/sim/control`, { data: { action: "speed", speed: 1 } })
  expect((await request.post(`${API}/sim/control`, { data: { action: "play" } })).ok()).toBeTruthy()
  // a fall type the Fall model reliably detects keeps the test deterministic (some SisFall types are missed: the UI says so honestly)
  await page.route(`${API}/sim/fall`, async (route) => route.continue({ postData: JSON.stringify({ ...route.request().postDataJSON(), activity: "F02", subject: "SA03" }) }))
  await page.goto("/", { waitUntil: "networkidle" })
  await expect(page.getByTestId("feed-conn")).toHaveText("live")
  await page.getByRole("link", { name: "Fall Monitor" }).first().click()
  await expect(page.getByTestId("simulate-fall")).toBeEnabled({ timeout: 20000 })
  await page.getByTestId("simulate-fall").click()
  const panel = page.getByTestId("alert-sent")
  await expect(panel.getByTestId("recipient")).toHaveCount(2, { timeout: 60000 })
  await expect(panel).toContainText("caregiver")
  await expect(panel).toContainText("clinician")
  await expect(page.getByTestId("sim-badge")).toContainText("Simulated fall signal")
  await page.getByRole("link", { name: "Dashboard" }).first().click()
  await expect(page.getByTestId("alert-item").first()).toContainText("URGENT")
  await expect(page.getByTestId("alert-item").first()).toContainText("Fall alert")
})

test("Demo guide: reset and play, then the fall step creates an URGENT alert", async ({ page }) => {
  await page.goto("/", { waitUntil: "networkidle" })
  await page.getByTestId("demo-guide-button").click()
  await page.getByTestId("demo-reset-play").click()
  await expect(page.getByRole("alertdialog")).toContainText("This clears the simulation tables")           // same confirmation as the Reset button
  await page.getByTestId("demo-confirm-reset").click()
  await expect(page.getByTestId("demo-step-1")).toContainText("Simulation reset and playing", { timeout: 30000 })
  await page.getByTestId("demo-fall").click()
  await expect(page.getByTestId("alert-sent").getByTestId("recipient")).toHaveCount(2, { timeout: 60000 })
  await page.getByRole("link", { name: "Dashboard" }).first().click()
  await expect(page.getByTestId("alert-item").first()).toContainText("URGENT")
})
