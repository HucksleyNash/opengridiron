import { defineConfig, devices } from "@playwright/test";
import { existsSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const testData = process.env.FOOTBALL_E2E_DATA_DIR || mkdtempSync(join(tmpdir(), "football-e2e-"));
process.env.FOOTBALL_E2E_DATA_DIR = testData;
const python = process.env.E2E_PYTHON || (existsSync("../.venv/bin/python") ? resolve("../.venv/bin/python") : "python");
const shellQuote = (value: string) => `'${value.replace(/'/g, "'\\''")}'`;
const backend = "http://127.0.0.1:18080";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  retries: 0,
  reporter: "line",
  use: {
    baseURL: "http://127.0.0.1:4173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    serviceWorkers: "block",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "tablet", use: { ...devices["iPad Mini"], browserName: "chromium" } },
    { name: "mobile", use: { ...devices["iPhone 13"], browserName: "chromium" } },
  ],
  webServer: [
    {
      command: `${shellQuote(python)} -m uvicorn app.main:app --app-dir ../backend --host 127.0.0.1 --port 18080`,
      env: { APP_ENV: "test", APP_SECRET: "draft-e2e-secret", AUTH_REQUIRED: "false", OWNER_PASSWORD: "draft-e2e-owner-password", SCHEDULER_ENABLED: "false", DATABASE_URL: `sqlite:///${join(testData, "football.sqlite3")}`, DATA_DIR: join(testData, "data") },
      url: `${backend}/healthz`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: "pnpm exec vite --host 127.0.0.1 --port 4173 --strictPort",
      env: { FOOTBALL_API_TARGET: backend },
      url: "http://127.0.0.1:4173",
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
