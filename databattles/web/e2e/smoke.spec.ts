import { expect, test } from "@playwright/test";

import { expectNoAppError, signIn } from "./helpers";

test.describe("public pages", () => {
  test("landing shows stats, featured competitions and demo labelling @mobile", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toContainText("learn, compete");
    await expect(page.getByText("Counts include synthetic demo data")).toBeVisible();
    await expect(page.getByRole("link", { name: /Crop Yield Forecasting Challenge/ }).first()).toBeVisible();
    await expectNoAppError(page);
  });

  test("competition discovery filters and detail", async ({ page }) => {
    await page.goto("/competitions");
    await expect(page.getByRole("link", { name: /Campus Energy Anomaly Detection/ }).first()).toBeVisible();
    await page.goto("/competitions/crop-yield-forecasting-challenge");
    await expect(page.getByRole("heading", { level: 1, name: "Crop Yield Forecasting Challenge" })).toBeVisible();
    await page.getByRole("link", { name: "Leaderboard" }).click();
    await expect(page.getByRole("table")).toBeVisible();
    await expectNoAppError(page);
  });

  test("members-only competition is hidden from visitors", async ({ page }) => {
    const res = await page.goto("/competitions/northbridge-internal-datathon");
    expect(res?.status()).toBeLessThan(500);
    await expect(page.getByText(/doesn't exist|not found/i).first()).toBeVisible();
  });

  test("certificate verification page", async ({ page, request }) => {
    const list = await request.get("/api/v1/users/ada-demo");
    const profile = await list.json();
    const cert = profile.certificates[0];
    await page.goto(`/verify/${cert.public_id}`);
    await expect(page.getByText(cert.event_title).first()).toBeVisible();
    await expect(page.getByText(/valid/i).first()).toBeVisible();
    await page.goto("/verify/DB-0000-0000-00");
    await expect(page.getByText(/not valid|no certificate/i).first()).toBeVisible();
  });

  test("protected pages redirect to sign in", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(page).toHaveURL(/\/login\?next=%2Fdashboard/);
  });
});

test.describe("student journey", () => {
  test("sign in, dashboard, submit predictions and see them scored", async ({ page, context }) => {
    await signIn(page, "student@example.com");
    await expect(page).toHaveURL(/\/dashboard/);
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Ada");
    await expectNoAppError(page);

    // Fetch the public sample submission through the signed-download flow (same as the UI does).
    const csrf = (await context.cookies()).find((c) => c.name === "db_csrf")?.value ?? "";
    const detail = await (await page.request.get("/api/v1/datasets/crop-yield-synthetic")).json();
    const sample = detail.files.find((f: { filename: string }) => f.filename === "sample_submission.csv");
    const signed = await page.request.post(`/api/v1/datasets/crop-yield-synthetic/files/${sample.id}/download`, {
      headers: { "X-CSRF-Token": csrf },
    });
    expect(signed.ok()).toBeTruthy();
    const csv = await (await page.request.get((await signed.json()).url)).body();

    await page.goto("/competitions/crop-yield-forecasting-challenge/submissions");
    await page.locator('input[type="file"]').setInputFiles({ name: "baseline.csv", mimeType: "text/csv", buffer: csv });
    await page.getByRole("button", { name: /Upload submission/ }).click();
    // The worker scores it in a sandboxed subprocess; the page polls until the status settles.
    await expect(page.getByRole("row", { name: /baseline\.csv/ }).first()).toContainText(/Scored/, { timeout: 30_000 });
    await expectNoAppError(page);
  });

  test("profile shows verified vs self-declared information", async ({ page }) => {
    await page.goto("/u/ada-demo");
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Ada Student");
    await expect(page.getByText(/Verified/).first()).toBeVisible();
    await expectNoAppError(page);
  });
});

test.describe("staff journeys", () => {
  test("organizer can open manage pages", async ({ page }) => {
    await signIn(page, "organizer@example.com");
    await page.goto("/organize");
    await expect(page.getByText("Crop Yield Forecasting Challenge").first()).toBeVisible();
    await page.goto("/competitions/crop-yield-forecasting-challenge/manage");
    await expectNoAppError(page);
    await page.goto("/competitions/crop-yield-forecasting-challenge/manage/analytics");
    await expectNoAppError(page);
  });

  test("admin health dashboard and moderation queue", async ({ page }) => {
    await signIn(page, "admin@example.com");
    await page.goto("/admin");
    await expect(page.getByText(/database/i).first()).toBeVisible();
    await page.goto("/moderation");
    await expect(page.getByText("Buy cheap followers now!!!").first()).toBeVisible();
  });

  test("judge sees assigned hackathon entries", async ({ page }) => {
    await signIn(page, "judge@example.com");
    await page.goto("/judge/build-for-good-hackathon-2026");
    await expect(page.getByText("SunTrack").first()).toBeVisible();
  });
});
