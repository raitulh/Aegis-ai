import { defineConfig, devices } from "@playwright/test";

const API_PORT = 8021;
const WEB_PORT = 3021;
const REPO = "../..";
const E2E_DB = process.env.E2E_DATABASE_URL || "postgresql://aegis:aegis@127.0.0.1:5432/aegis_e2e";
const E2E_ADMIN_DB = process.env.E2E_DATABASE_ADMIN_URL || "postgresql://postgres:postgres@127.0.0.1:5432/aegis_e2e";

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  retries: 0,
  reporter: process.env.CI ? "github" : "line",
  use: { baseURL: `http://localhost:${WEB_PORT}`, trace: "on-first-retry" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  // Playwright manages both the API and web servers for the run (no external orchestration needed).
  webServer: [
    {
      command: `bash -c 'cd ${REPO} && PYTHONPATH=apps/api DATABASE_URL="${E2E_DB}" DATABASE_ADMIN_URL="${E2E_ADMIN_DB}" JOB_BACKEND=inline RATE_LIMIT_ENABLED=false ENVIRONMENT=development CORS_ORIGINS="http://localhost:${WEB_PORT}" uv run uvicorn aegis_api.app:app --host 127.0.0.1 --port ${API_PORT} --log-level warning'`,
      url: `http://127.0.0.1:${API_PORT}/health`,
      timeout: 60_000,
      reuseExistingServer: false,
    },
    {
      command: `API_INTERNAL_URL=http://127.0.0.1:${API_PORT} npx next start -p ${WEB_PORT}`,
      url: `http://localhost:${WEB_PORT}`,
      timeout: 60_000,
      reuseExistingServer: false,
    },
  ],
});
