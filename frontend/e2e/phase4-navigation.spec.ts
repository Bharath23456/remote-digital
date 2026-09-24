import { expect, test } from "@playwright/test";

test("administrator can open revaluation and photocopy request screens", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Email address").fill("admin@admiezo.local");
  await page.getByLabel("Password").fill("ChangeMe123!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Evaluation operations" })).toBeVisible();

  const navigation = page.getByRole("navigation", { name: "Primary navigation" });
  await expect(navigation.getByRole("button", { name: "Revaluation & recounting", exact: true })).toBeVisible();
  await expect(navigation.getByRole("button", { name: "Photocopy requests", exact: true })).toBeVisible();

  await navigation.getByRole("button", { name: "Revaluation & recounting", exact: true }).click();
  await expect(page.getByRole("button", { name: "Revaluation & recounting", exact: true }).last()).toBeVisible();

  await navigation.getByRole("button", { name: "Photocopy requests", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Photocopy requests" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Application requests", exact: true })).toBeVisible();
});
