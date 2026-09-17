import { expect, test } from "@playwright/test";

test("evaluator can open personal password settings", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Northbridge University" })).toBeVisible();
  await page.getByLabel("Email address").fill("evaluator1045@admiezo.local");
  await page.getByLabel("Password").fill("ChangeMe123!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByRole("button", { name: "Account settings" }).click();
  const dialog = page.getByRole("dialog", { name: "Account settings" });
  await expect(dialog.getByText("evaluator1045@admiezo.local")).toBeVisible();
  await expect(dialog.getByLabel("Current password")).toBeVisible();
  await expect(dialog.getByLabel("New password", { exact: true })).toBeVisible();
  await expect(dialog.getByText("Not enrolled")).toBeVisible();
});

test("administrator reaches guarded authenticator reset", async ({ page }) => {
  await page.route("**/api/v1/security/catalog", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    const evaluator = body.members.find((member: { email: string }) => member.email === "evaluator1045@admiezo.local");
    if (evaluator) { evaluator.authenticator_enabled = true; evaluator.can_reset_authenticator = true; }
    await route.fulfill({ response, json: body });
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Northbridge University" })).toBeVisible();
  await page.getByLabel("Email address").fill("admin@admiezo.local");
  await page.getByLabel("Password").fill("ChangeMe123!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  if ((page.viewportSize()?.width || 0) <= 760) {
    await page.getByTitle("Open navigation").click();
    await page.getByRole("navigation", { name: "Primary navigation" }).getByRole("button", { name: "Access governance", exact: true }).evaluate((button: HTMLButtonElement) => button.click());
  } else {
    await page.getByRole("navigation", { name: "Primary navigation" }).getByRole("button", { name: "Access governance", exact: true }).click();
  }
  await page.getByRole("button", { name: "Access", exact: true }).click();
  const userAccess = page.locator("section.panel").filter({ has: page.getByRole("heading", { name: "User access" }) });
  await userAccess.locator("tbody tr").filter({ hasText: "evaluator1045@admiezo.local" }).getByRole("button", { name: "Edit access" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Reset authenticator" }).click();
  const stepUp = page.getByRole("dialog");
  await stepUp.getByLabel("Password").fill("ChangeMe123!");
  await stepUp.getByRole("button", { name: "Verify" }).click();
  const confirmation = page.getByRole("dialog");
  await expect(confirmation.getByRole("heading", { name: "Reset authenticator" })).toBeVisible();
  await expect(confirmation.getByText("evaluator1045@admiezo.local")).toBeVisible();
  await page.unrouteAll({ behavior: "ignoreErrors" });
});
