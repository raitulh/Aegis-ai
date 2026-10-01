import { expect, type Page } from "@playwright/test";

export const DEMO_PASSWORD = "DemoPass!2026";

export async function signIn(page: Page, email: string, password = DEMO_PASSWORD) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel(/^Password/).fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"));
}

export async function expectNoAppError(page: Page) {
  await expect(page.getByText("Something went wrong", { exact: false })).toHaveCount(0);
}
