import { expect, test } from "@playwright/test";

test("manual assignment and simulation offer only configured valuation rounds", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Email address").fill("controller@admiezo.local");
  await page.getByLabel("Password").fill("ChangeMe123!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Evaluation operations" })).toBeVisible();

  await page.route("**/api/v1/allocation/catalog", async (route) => {
    const response = await route.fetch();
    const catalog = await response.json();
    catalog.scripts = [
      { id: "script-one", script_code: "AS-ONE", paper_id: "paper-one", paper: "MA301-A", state: "stored", version: 1, assigned_rounds: [] },
      { id: "script-two", script_code: "AS-TWO", paper_id: "paper-two", paper: "CS401-A", state: "stored", version: 1, assigned_rounds: [1] },
    ];
    catalog.papers = [
      { id: "paper-one", code: "MA301-A", title: "One round", valuation_rounds: 1, stored_scripts: 1, status: "frozen" },
      { id: "paper-two", code: "CS401-A", title: "Two rounds", valuation_rounds: 2, stored_scripts: 1, status: "frozen" },
    ];
    await route.fulfill({ response, json: catalog });
  });

  const navigation = page.getByRole("navigation", { name: "Primary navigation" });
  if ((page.viewportSize()?.width || 1280) < 900) await page.getByTitle("Open navigation").click();
  await navigation.getByRole("button", { name: "Allocation engine", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Allocation engine", exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Manual", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Anonymous script").selectOption("script-one");
  await expect(dialog.getByLabel("Valuation round").locator("option")).toHaveCount(1);
  await expect(dialog.getByLabel("Valuation round")).toHaveValue("1");
  await dialog.getByLabel("Anonymous script").selectOption("script-two");
  await expect(dialog.getByLabel("Valuation round").locator("option")).toHaveCount(1);
  await expect(dialog.getByLabel("Valuation round")).toHaveValue("2");
  await dialog.getByTitle("Close").click();

  await page.getByRole("button", { name: "Simulate allocation" }).click();
  await dialog.getByLabel("Frozen paper").selectOption("paper-one");
  await expect(dialog.getByLabel("Valuation round").locator("option")).toHaveCount(1);
  await expect(dialog.getByLabel("Valuation round")).toHaveValue("1");
  await dialog.getByLabel("Frozen paper").selectOption("paper-two");
  await expect(dialog.getByLabel("Valuation round").locator("option")).toHaveCount(2);
});
