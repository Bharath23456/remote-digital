import { expect, test } from "@playwright/test";

test("simulation offers only configured rounds and no manual assignment", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Email address").fill("admin@admiezo.local");
  await page.getByLabel("Password").fill("ChangeMe123!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Evaluation operations" })).toBeVisible();

  await page.route("**/api/v1/allocation/catalog", async (route) => {
    const response = await route.fetch();
    const catalog = await response.json();
    catalog.scripts = [
      { id: "script-one", script_code: "AS-ONE", paper_id: "paper-one", paper: "MA301-A", state: "stored", version: 1, assigned_rounds: [], next_round: 1 },
      { id: "script-two", script_code: "AS-TWO", paper_id: "paper-two", paper: "CS401-A", state: "submitted", version: 1, assigned_rounds: [1], next_round: 2 },
      { id: "script-trigger", script_code: "AS-TRIGGER", paper_id: "paper-trigger", paper: "PH301-A", state: "submitted", version: 1, assigned_rounds: [1], next_round: 2 },
      { id: "script-complete", script_code: "AS-COMPLETE", paper_id: "paper-one", paper: "MA301-A", state: "submitted", version: 1, assigned_rounds: [1], next_round: null },
    ];
    catalog.papers = [
      { id: "paper-one", code: "MA301-A", title: "One round", valuation_rounds: 1, second_valuation_mark_threshold: null, stored_scripts: 1, status: "frozen" },
      { id: "paper-two", code: "CS401-A", title: "Two rounds", subject_code: "CS401", subject_name: "Computer Science", valuation_rounds: 2, second_valuation_mark_threshold: null, stored_scripts: 1, status: "frozen" },
      { id: "paper-trigger", code: "PH301-A", title: "Conditional second round", valuation_rounds: 1, second_valuation_mark_threshold: "75.00", stored_scripts: 0, status: "frozen" },
    ];
    await route.fulfill({ response, json: catalog });
  });

  const navigation = page.getByRole("navigation", { name: "Primary navigation" });
  if ((page.viewportSize()?.width || 1280) < 900) await page.getByTitle("Open navigation").click();
  await navigation.getByRole("button", { name: "Allocation engine", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Allocation engine", exact: true })).toBeVisible();

  await expect(page.getByRole("button", { name: "Manual", exact: true })).toHaveCount(0);
  const dialog = page.getByRole("dialog");
  await page.getByRole("button", { name: "Simulate allocation" }).click();
  await dialog.getByLabel("Frozen paper").selectOption("paper-one");
  await expect(dialog.getByLabel("Valuation round").locator("option")).toHaveCount(1);
  await expect(dialog.getByLabel("Valuation round")).toHaveValue("1");
  await dialog.getByLabel("Frozen paper").selectOption("paper-two");
  await expect(dialog.getByLabel("Valuation round").locator("option")).toHaveCount(2);
  await expect(dialog.getByText(/Subject: CS401.*Computer Science/)).toBeVisible();
  await dialog.getByLabel("Frozen paper").selectOption("paper-trigger");
  await expect(dialog.getByLabel("Valuation round").locator("option")).toHaveCount(2);
});
