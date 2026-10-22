import { defineConfig } from "@playwright/test"

// The e2e tests own their whole stack: a fresh throw-away database (data/echos_e2e.db), an API on port 8010 and a Vite server on 5180.
// They never touch data/echos_demo.db or the dev ports (8000 / 5173). Backend: dry-run mail. Needs models/v2 (scripts/rebuild_models.sh).
const API = "http://127.0.0.1:8010"
const WEB = "http://localhost:5180"
export default defineConfig({
  testDir: "./e2e",
  timeout: 150_000,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: { baseURL: WEB, viewport: { width: 1440, height: 900 }, trace: "off" },
  webServer: [
    {
      command: `bash -c "cd .. && rm -f data/echos_e2e.db data/echos_e2e.db-wal data/echos_e2e.db-shm && ECHOS_API_PORT=8010 ECHOS_CORS_ORIGIN=${WEB} ECHOS_CAREGIVER_EMAIL=caregiver@example.test ECHOS_CLINICIAN_EMAIL=clinician@example.test ECHOS_ESCALATION_EMAIL=escalation@example.test scripts/run_api.sh --dry --demo --db data/echos_e2e.db --days 10"`,
      url: `${API}/api/sim/state`, timeout: 120_000, reuseExistingServer: false, gracefulShutdown: { signal: "SIGTERM", timeout: 15_000 },
    },
    { command: `VITE_API_URL=${API} npx vite --host 127.0.0.1 --port 5180 --strictPort`, url: "http://127.0.0.1:5180", timeout: 60_000, reuseExistingServer: false },
  ],
})
