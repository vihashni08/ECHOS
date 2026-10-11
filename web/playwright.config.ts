import { defineConfig } from "@playwright/test"

// Starts nothing: run the API (scripts/run_api.sh --dry --no-llm --demo --db data/echos_ui_dev.db) and `npm run dev` first.
export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: { baseURL: process.env.WEB_URL ?? "http://localhost:5173", viewport: { width: 1440, height: 900 }, trace: "off" },
})
