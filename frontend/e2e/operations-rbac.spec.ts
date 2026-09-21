import { expect, test } from "@playwright/test";

test("admin assigns intake roles and supervisor sees only intake operations", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Email address").fill("admin@admiezo.local");
  await page.getByLabel("Password").fill("ChangeMe123!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Evaluation operations" })).toBeVisible();
  await page.getByRole("navigation", { name: "Primary navigation" }).getByRole("button", { name: "Access governance" }).click();
  await page.getByRole("button", { name: "Access", exact: true }).click();
  await page.getByRole("button", { name: "Re-authenticate" }).click();
  await page.getByLabel("Password").fill("ChangeMe123!");
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page.getByText("Step-up active")).toBeVisible();
  await page.getByRole("button", { name: "Access", exact: true }).click();
  await page.getByRole("button", { name: "Add user" }).click();

  const dialog = page.getByRole("dialog");
  const role = dialog.getByLabel("Role");
  const modules = dialog.locator("fieldset.module-entitlement-grid");
  for (const [value, expected] of [
    ["bundle_preparer", ["receiving"]],
    ["intake_receiver", ["custody"]],
    ["scan_operator", ["digitization"]],
    ["operations_supervisor", ["receiving", "custody", "digitization"]],
  ] as const) {
    await role.selectOption(value);
    await expect(modules.locator('input[type="hidden"][name="modules"]')).toHaveCount(expected.length);
    for (const moduleName of expected) {
      await expect(modules.locator(`input[type="hidden"][value="${moduleName}"]`)).toHaveCount(1);
      await expect(modules.locator(`input[type="checkbox"][value="${moduleName}"]`)).toBeDisabled();
    }
  }

  const email = `playwright.intake.supervisor.${Date.now()}@example.edu`;
  await dialog.getByLabel("First name").fill("Intake");
  await dialog.getByLabel("Last name").fill("Supervisor");
  await dialog.getByLabel("Institutional email").fill(email);
  await dialog.getByRole("button", { name: "Create user" }).click();
  const credentials = page.getByRole("dialog");
  await expect(credentials.getByRole("heading", { name: "User login ready" })).toBeVisible();
  const temporaryPassword = await credentials.locator(".credential-sheet label").filter({ hasText: "Temporary password" }).locator("code").innerText();
  await credentials.getByRole("button", { name: "Done" }).click();
  await page.getByTitle("Sign out").click();

  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password").fill(temporaryPassword);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Set your password" })).toBeVisible();
  await page.getByLabel("New password").fill("StrongTrialPass123!");
  await page.getByLabel("Confirm password").fill("StrongTrialPass123!");
  await page.getByRole("button", { name: "Set password and continue" }).click();
  await expect(page.getByRole("heading", { name: "Intake operations" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Bundle progress" })).toBeVisible();

  const navigation = page.getByRole("navigation", { name: "Primary navigation" });
  for (const label of ["Command centre", "Script receiving", "Chain of custody", "Digitization"]) {
    await expect(navigation.getByRole("button", { name: label, exact: true })).toBeVisible();
  }
  for (const label of ["Live operations", "Exam configuration", "Evaluation desk", "Access governance"]) {
    await expect(navigation.getByRole("button", { name: label, exact: true })).toHaveCount(0);
  }
  await navigation.getByRole("button", { name: "Chain of custody" }).click();
  await expect(page.getByRole("heading", { name: "Chain of custody" })).toBeVisible();
  await navigation.getByRole("button", { name: "Digitization" }).click();
  await expect(page.getByRole("heading", { name: "Digitization control" })).toBeVisible();
});
