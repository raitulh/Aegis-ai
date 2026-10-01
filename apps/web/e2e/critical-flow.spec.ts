import { expect, test } from "@playwright/test";

/**
 * Critical end-to-end flow against a running stack (web + API + DB).
 * Signs up, registers a simulated system, runs an audit, and inspects a finding with evidence.
 * Run with: E2E_BASE_URL=http://localhost:3000 pnpm --filter @aegis/web test:e2e
 */
test("landing page renders the hero without fabricated metrics", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: /Know how your AI behaves/i })).toBeVisible();
  await expect(page.getByRole("button", { name: /Try the sandbox/i }).first()).toBeVisible();
  await expect(page.getByText(/certified|guaranteed compliance/i)).toHaveCount(0);
});

test("pricing and trust pages render from the API", async ({ page }) => {
  await page.goto("/pricing");
  await expect(page.getByRole("heading", { name: "Free" })).toBeVisible();
  await page.goto("/trust");
  await expect(page.getByRole("heading", { name: /Certifications and attestations/i })).toBeVisible();
  await expect(page.getByText(/not attested|not certified/i).first()).toBeVisible();
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

  // Wait for completion: the completed view shows the findings tab and the evidence package action.
  await expect(page.getByRole("tab", { name: "Findings" })).toBeVisible({ timeout: 60000 });
  await page.getByRole("tab", { name: "Evidence" }).click();
  await expect(page.getByText("Verified").first()).toBeVisible({ timeout: 15000 });

  // The new sections render for a fresh workspace (owner role).
  for (const [path, heading] of [
    ["/dashboard/runtime", /Runtime Guard/i],
    ["/dashboard/policies", /Policies & controls/i],
    ["/dashboard/evidence", /Evidence/i],
    ["/dashboard/billing", /Usage & billing/i],
    ["/dashboard/graph", /Assurance graph/i],
  ] as const) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
  }
});
