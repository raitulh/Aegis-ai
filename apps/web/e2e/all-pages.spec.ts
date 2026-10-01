import { expect, test } from "@playwright/test";

/**
 * Smoke test across the whole dashboard using the DEMO sandbox (real engines, simulated systems):
 * every page must render its heading without an uncaught error or an error state.
 */
const PAGES: [string, RegExp][] = [
  ["/dashboard", /AI Trust Posture/i],
  ["/dashboard/graph", /Assurance graph/i],
  ["/dashboard/systems", /AI systems/i],
  ["/dashboard/agents", /Agents/i],
  ["/dashboard/runtime", /Runtime Guard/i],
  ["/dashboard/runtime?tab=approvals", /Runtime Guard/i],
  ["/dashboard/runtime?tab=modes", /Runtime Guard/i],
  ["/dashboard/audits", /Audits/i],
  ["/dashboard/red-team", /Red Team/i],
  ["/dashboard/assurance", /Continuous assurance/i],
  ["/dashboard/monitoring", /Monitoring/i],
  ["/dashboard/findings", /Findings/i],
  ["/dashboard/policies", /Policies & controls/i],
  ["/dashboard/policies?tab=compliance", /Policies & controls/i],
  ["/dashboard/policies?tab=library", /Policies & controls/i],
  ["/dashboard/evidence", /Evidence/i],
  ["/dashboard/evidence?tab=records", /Evidence/i],
  ["/dashboard/evidence?tab=verify", /Evidence/i],
  ["/dashboard/reports", /Reports/i],
  ["/dashboard/integrations", /Integrations/i],
  ["/dashboard/developers", /API & SDK/i],
  ["/dashboard/team", /Members & roles/i],
  ["/dashboard/billing", /Usage & billing/i],
  ["/dashboard/audit-log", /Audit log/i],
  ["/dashboard/settings", /Settings/i],
];

test("every dashboard page renders in the DEMO sandbox", async ({ page }) => {
  test.setTimeout(240_000);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(`${page.url()}: ${e.message}`));

  await page.goto("/demo");
  await page.getByRole("button", { name: /Start the sandbox/i }).click();
  await expect(page).toHaveURL(/\/dashboard/, { timeout: 120_000 });
  await expect(page.getByText("DEMO").first()).toBeVisible();

  for (const [path, heading] of PAGES) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1, name: heading }), path).toBeVisible();
    await expect(page.getByText(/Something went wrong/i), path).toHaveCount(0);
  }

  // Drill into detail pages from real rows.
  await page.goto("/dashboard/findings");
  await page.locator("tbody a").first().click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("#");
  await expect(page.getByText("Lifecycle")).toBeVisible();

  await page.goto("/dashboard/audits");
  await page.locator("tbody a").first().click();
  await expect(page.getByRole("tab", { name: "Evidence" })).toBeVisible({ timeout: 60_000 });

  await page.goto("/dashboard/systems");
  await page.locator("tbody a").first().click();
  await expect(page.getByRole("tab", { name: "Overview" })).toBeVisible();

  await page.goto("/dashboard/graph");
  await page.getByRole("radio", { name: "List" }).click();
  await page.locator("section button").first().click();
  await expect(page.getByText(/^\d+ relationships$/)).toBeVisible();

  expect(errors, errors.join("\n")).toEqual([]);
});
