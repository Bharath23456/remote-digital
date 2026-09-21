import { expect, test } from "@playwright/test";

test("ready scripts are visible and allocation refresh reloads them", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Email address").fill("admin@admiezo.local");
  await page.getByLabel("Password").fill("ChangeMe123!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Evaluation operations" })).toBeVisible();

  let scriptCode = "AS-READY-001";
  await page.route("**/api/v1/allocation/catalog", async (route) => {
    await route.fulfill({ json: {
      assignments: [],
      scripts: [{ id: "script-1", script_code: scriptCode, paper_id: "paper-1", paper: "CS301-A", state: "stored", version: 1, assigned_rounds: [], next_round: 1 }],
      papers: [{ id: "paper-1", code: "CS301-A", title: "Computer Science", valuation_rounds: 1, second_valuation_mark_threshold: null, stored_scripts: 1, status: "frozen" }], evaluators: [], policies: [], runs: [],
    } });
  });

  await page.getByRole("navigation", { name: "Primary navigation" }).getByRole("button", { name: "Allocation engine" }).click();
  const ready = page.locator("section.panel").filter({ has: page.getByRole("heading", { name: "Ready for allocation" }) });
  await expect(ready.getByRole("row", { name: /AS-READY-001/ })).toBeVisible();
  await expect(page.getByText("No assignments records yet.")).toBeVisible();

  await ready.getByRole("button", { name: "Simulate" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("heading", { name: "Allocation simulation" })).toBeVisible();
  await expect(dialog.getByLabel("Frozen paper")).toHaveValue("paper-1");
  await expect(dialog.getByLabel("Valuation round")).toHaveValue("1");
  await dialog.getByTitle("Close").click();

  scriptCode = "AS-READY-002";
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(ready.getByRole("row", { name: /AS-READY-002/ })).toBeVisible();
  await expect(ready.getByRole("row", { name: /AS-READY-001/ })).toHaveCount(0);
});
