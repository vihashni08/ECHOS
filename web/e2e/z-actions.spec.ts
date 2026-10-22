import { expect, test } from "@playwright/test"
import { json, replayToEnd } from "./helpers"

test.beforeAll(async () => { await replayToEnd() })

test("inject a red-flag reading from Demo tools -> URGENT alert on the Alerts page", async ({ page }) => {
  const before = (await json<{ total: number }>("/alerts")).total
  await page.goto("/alerts")
  await page.getByTestId("demo-tools-button").click()
  await page.getByTestId("inject-reading").click()
  await expect(page.getByTestId("inject-result")).toContainText("Sent to the Patient Monitor")
  await page.keyboard.press("Escape")
  await expect.poll(async () => (await json<{ total: number }>("/alerts")).total, { timeout: 15000 }).toBe(before + 1)
  const newest = (await json<{ items: { alert_id: string; time: number }[] }>("/alerts?limit=1")).items[0].alert_id
  const card = page.locator(`[data-alert-id="${newest}"]`)
  await expect(card).toContainText("Red-flag reading")
  await card.getByTestId("ack").click()
  await expect.poll(async () => (await json<{ items: { alert_id: string; state: string }[] }>("/alerts?limit=50")).items.find((a) => a.alert_id === newest)?.state ?? "",
    { timeout: 10000 }).toBe("ACKNOWLEDGED")
  await page.getByTestId("tab-all").click()
  await expect(page.getByTestId("alert-state").filter({ hasText: "ACKNOWLEDGED" }).first()).toBeVisible()
})

test("kill the Review Coordinator: the supervisor brings it back", async ({ page }) => {
  await page.goto("/agents")
  await page.getByRole("button", { name: "Kill Review Coordinator agent" }).click()
  await page.getByRole("button", { name: "Kill agent", exact: true }).click()
  await expect(page.getByTestId("kill-result")).toContainText("alive again", { timeout: 90000 })
})
