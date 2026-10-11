import { expect, test, type Page } from "@playwright/test"

const API = process.env.API_URL ?? "http://127.0.0.1:8000/api"
const ROUTES = ["/", "/falls", "/patient", "/caregiver", "/clinician", "/system"]

function watchConsole(page: Page) {
  const errors: string[] = []
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()))
  page.on("pageerror", (e) => errors.push(String(e)))
  return errors
}

test("each page loads without console errors", async ({ page }) => {
  const errors = watchConsole(page)
  for (const r of ROUTES) {
    await page.goto(r, { waitUntil: "networkidle" })
    await expect(page.locator("main h1").first()).toBeVisible()
  }
  const first = ((await (await fetch(`${API}/patients`)).json()) as { items: { patient_id: string }[] }).items[0].patient_id
  await page.goto(`/patients/${first}`, { waitUntil: "networkidle" })
  await expect(page.getByTestId("why-flagged")).toBeVisible()
  await page.getByRole("tab", { name: /routine/i }).click()
  await expect(page.getByTestId("routine-charts")).toBeVisible()
  expect(errors, errors.join("\n")).toEqual([])
})

test("theme toggle switches and is remembered", async ({ page }) => {
  await page.goto("/")
  const html = page.locator("html")
  const was = await html.evaluate((e) => e.classList.contains("dark"))
  await page.getByTestId("theme-toggle").click()
  await expect(html).toHaveClass(was ? /^((?!dark).)*$/ : /dark/)
  await page.reload()
  expect(await html.evaluate((e) => e.classList.contains("dark"))).toBe(!was)
  await page.getByTestId("theme-toggle").click()
  expect(await html.evaluate((e) => e.classList.contains("dark"))).toBe(was)
})

test("API-down banner appears when the API is unreachable", async ({ page }) => {
  await page.route(`${API}/**`, (r) => r.abort())
  await page.goto("/")
  await expect(page.getByTestId("api-banner")).toContainText("API not reachable: start scripts/run_api.sh")
})

test("Simulate fall raises an URGENT alert in the feed and shows the Alert sent panel with two recipients", async ({ page, request }) => {
  // fresh, slow simulation so an open day exists for the injection (this clears the dev database's simulation tables)
  await request.post(`${API}/sim/control`, { data: { action: "reset" } })
  await request.post(`${API}/sim/control`, { data: { action: "speed", speed: 1 } })
  expect((await request.post(`${API}/sim/control`, { data: { action: "play" } })).ok()).toBeTruthy()
  // a fall type the Fall model reliably detects keeps the test deterministic (some SisFall types are missed: that is shown honestly in the UI)
  await page.route(`${API}/sim/fall`, async (route) => route.continue({ postData: JSON.stringify({ ...route.request().postDataJSON(), activity: "F02", subject: "SA03" }) }))
  await page.goto("/", { waitUntil: "networkidle" })
  await expect(page.getByTestId("feed-conn")).toHaveText("live")
  await page.getByRole("link", { name: "Fall Monitor" }).first().click()      // client-side navigation keeps the SSE feed state
  await expect(page.getByTestId("simulate-fall")).toBeEnabled({ timeout: 20000 })
  await page.getByTestId("simulate-fall").click()
  const panel = page.getByTestId("alert-sent")
  await expect(panel.getByTestId("recipient")).toHaveCount(2, { timeout: 45000 })
  await expect(panel).toContainText("caregiver")
  await expect(panel).toContainText("clinician")
  await expect(page.getByTestId("sim-badge")).toContainText("Simulated fall signal")
  await page.getByRole("link", { name: "Dashboard" }).first().click()
  const item = page.getByTestId("alert-item").first()
  await expect(item).toContainText("URGENT")
  await expect(item).toContainText("Fall alert")
})
