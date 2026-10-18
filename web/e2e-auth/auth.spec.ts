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
  await expect(page.getByRole("link", { name: "Alerts" }).first()).toBeVisible()
  await expect(page.getByRole("link", { name: "Clinician week" })).toHaveCount(0)
  await expect(page.getByTestId("demo-tools-button")).toHaveCount(0)
  await page.goto("/system", { waitUntil: "networkidle" })           // a fresh load keeps the session (sessionStorage) but not the page
  await expect(page.getByRole("tab", { name: "How it works" })).toBeVisible()      // the explanation tabs are open to every role ...
  await expect(page.getByRole("tab", { name: "Health" })).toHaveCount(0)            // ... the health and metrics tabs are operator-only
  await page.goto("/clinician", { waitUntil: "networkidle" })
  await expect(page.getByTestId("forbidden")).toContainText("clinician")
  await page.getByTestId("logout").click()
  await expect(page.getByTestId("token")).toBeVisible()
})

test("an operator sees everything including the emulator controls", async ({ page }) => {
  await page.goto("/", { waitUntil: "networkidle" })
  await login(page, "e2e-oper")
  await expect(page.getByTestId("role")).toContainText("operator")
  for (const n of ["Clinician week", "System"]) await expect(page.getByRole("link", { name: n }).first()).toBeVisible()
  await page.getByTestId("demo-tools-button").click()
  await page.getByTestId("emu-play").click()
  await expect(page.getByTestId("emu-status")).toContainText("playing", { timeout: 20000 })      // the event stream and API calls work with the bearer token
  await expect(page.getByTestId("conn")).toContainText("Live")
})
