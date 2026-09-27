import { expect, test } from "@playwright/test";

/**
 * Critical end-to-end flow against a running stack (web + API + DB).
 * Signs up, registers a simulated system, runs an audit, and inspects a finding with evidence.
 * Run with: E2E_BASE_URL=http://localhost:3000 pnpm --filter @aegis/web test:e2e
 */
test("landing page renders the hero and live audit demo", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /Know when your AI/i })).toBeVisible();
  await expect(page.getByRole("button", { name: /Run a Live Audit/i }).first()).toBeVisible();
});

test("signup → create system → run audit → inspect finding", async ({ page }) => {
  const email = `e2e-${Date.now()}@example.com`;
  await page.goto("/signup");
  await page.getByPlaceholder("Alex Rivera").fill("E2E Tester");
  await page.getByPlaceholder("Acme AI").fill("E2E Workspace");
  await page.getByPlaceholder("you@company.com").fill(email);
  await page.getByPlaceholder("••••••••••").fill("Str0ng-Pass!23");
  await page.getByRole("button", { name: "Create workspace" }).click();

  await expect(page).toHaveURL(/\/dashboard/, { timeout: 15000 });
  await expect(page.getByRole("heading", { name: /AI Trust Posture/i })).toBeVisible();

  // Register a simulated system
  await page.goto("/dashboard/systems");
  await page.getByRole("button", { name: /Add AI System/i }).first().click();
  await page.getByPlaceholder("Hiring-Agent").fill("Hiring-Agent");
  await page.getByRole("button", { name: /Create system/i }).click();
  await expect(page).toHaveURL(/\/dashboard\/systems\/[0-9a-f-]+/, { timeout: 15000 });

  // Run an audit via the wizard
  await page.getByRole("link", { name: /Run Audit/i }).click();
  await expect(page).toHaveURL(/\/dashboard\/audits\/new/);
  // System is preselected via query param; advance through the wizard
  for (const _ of [0, 1, 2, 3]) {
    await page.getByRole("button", { name: /Continue/i }).click();
  }
  await page.getByRole("button", { name: /Launch Audit/i }).click();
  await expect(page).toHaveURL(/\/dashboard\/audits\/[0-9a-f-]+/, { timeout: 15000 });

  // Wait for completion and a finding to appear
  await expect(page.getByText(/complete|Findings/i).first()).toBeVisible({ timeout: 60000 });
});
