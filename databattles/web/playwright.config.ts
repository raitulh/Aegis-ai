import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests against a running stack (API + worker + web) seeded with demo data:
 *   make seed && make dev   (or docker compose up)   then   npm run test:e2e
 * Set E2E_BASE_URL to target another deployment. CHROMIUM_PATH lets CI use a preinstalled browser.
 */
export default defineConfig({
  testDir: "./e2e",
  outputDir: "./e2e/.results",
  timeout: 45_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    launchOptions: process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : undefined,
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["Pixel 7"] }, grep: /@mobile/ },
  ],
});
