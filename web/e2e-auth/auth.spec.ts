import AxeBuilder from "@axe-core/playwright"
import { expect, test } from "@playwright/test"
import type { Page } from "@playwright/test"

const login = async (page: Page, token: string) => { await page.getByTestId("token").fill(token); await page.getByTestId("login").click() }

test("without a token only the login page is shown; a wrong token is refused", async ({ page }) => {
  await page.goto("/", { waitUntil: "networkidle" })
  await expect(page.getByTestId("token")).toBeVisible()
  await expect(page.getByRole("link", { name: "Alerts" })).toHaveCount(0)
  await login(page, "wrong")
  await expect(page.getByTestId("login-error")).toContainText("wrong token")
  await page.waitForTimeout(600)                                     // let the button's colour transition finish before the contrast check
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze()
  expect(r.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toEqual([])
})

test("a caregiver sees the care pages only and is stopped from the rest; log out returns to the login page", async ({ page }) => {
  await page.goto("/", { waitUntil: "networkidle" })
  await login(page, "e2e-care")
  await expect(page.getByTestId("role")).toContainText("caregiver")
  for (const n of ["Daily review", "Patients", "Report & references"]) await expect(page.getByRole("link", { name: n }).first()).toBeVisible()
  await expect(page.locator("aside").getByRole("link", { name: "System (4 agents)" })).toHaveCount(0)
  await expect(page.getByTestId("demo-tools-button")).toHaveCount(0)
  await page.goto("/how", { waitUntil: "networkidle" })
  await expect(page.getByTestId("results")).toBeVisible()           // evaluation results are open to every role
  await page.goto("/agents", { waitUntil: "networkidle" })
  await expect(page.getByTestId("forbidden")).toContainText("clinician")
  await page.getByTestId("logout").click()
  await expect(page.getByTestId("token")).toBeVisible()
})

test("an operator sees everything and can drive the replay", async ({ page }) => {
  await page.goto("/", { waitUntil: "networkidle" })
  await login(page, "e2e-oper")
  await expect(page.getByTestId("role")).toContainText("operator")
  await expect(page.getByRole("link", { name: "System (4 agents)" }).first()).toBeVisible()
  await page.getByTestId("demo-tools-button").click()
  await page.getByTestId("play-pause").click()
  await expect(page.getByTestId("sim-state")).toContainText(/playing|finished/, { timeout: 20000 })     // API calls work with the bearer token
  await expect(page.getByTestId("conn")).toContainText("Live")                                          // and so does the event stream
})
