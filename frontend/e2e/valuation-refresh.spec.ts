import { expect, test } from "@playwright/test";

test("valuation review has one refresh button that reloads its data", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Email address").fill("controller@admiezo.local");
  await page.getByLabel("Password").fill("ChangeMe123!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Evaluation operations" })).toBeVisible();

  await page.getByRole("navigation", { name: "Primary navigation" }).getByRole("button", { name: "Valuation review", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Valuation review", exact: true })).toBeVisible();
  const refresh = page.getByRole("button", { name: "Refresh", exact: true });
  await expect(refresh).toHaveCount(1);
  await expect(refresh).toBeEnabled();

  const reloaded = page.waitForResponse((response) => response.url().includes("/api/v1/valuation/catalog") && response.ok());
  await refresh.click();
  await reloaded;
});
