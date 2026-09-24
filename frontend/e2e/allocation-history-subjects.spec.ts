import { expect, test } from "@playwright/test";

async function signIn(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByLabel("Email address").fill("admin@admiezo.local");
  await page.getByLabel("Password").fill("ChangeMe123!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Evaluation operations" })).toBeVisible();
}

test("allocation history shows the simulation operator and subject without edit controls", async ({ page }) => {
  await signIn(page);
  let scriptCode = "AS-DEMO-1";
  await page.route("**/api/v1/allocation/history?**", (route) => route.fulfill({ json: { total: 1, page: 1, page_size: 50, rows: [{
    id: "proposal-1", run_id: "run-1", ran_by: "Exam Admin", ran_at: "2026-09-19T10:00:00Z",
    script: scriptCode, paper: "CS401-A", subject: "CS401", round: 1,
    evaluator: "Dr. Computer", status: "completed", blockers: [],
  }] } }));
  await page.getByRole("navigation", { name: "Primary navigation" }).getByRole("button", { name: "Allocation history" }).click();
  await expect(page.getByRole("row", { name: /Exam Admin.*AS-DEMO-1.*CS401-A.*CS401.*Dr\. Computer.*Committed/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Approve|Reassign|Assign/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Refresh", exact: true })).toHaveCount(1);
  scriptCode = "AS-DEMO-2";
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(page.getByRole("row", { name: /AS-DEMO-2/ })).toBeVisible();
});

test("evaluator subject search retains hidden selections in the create request", async ({ page }) => {
  await signIn(page);
  const subjects = [
    { id: "subject-computer", code: "CS401", name: "Computer Science" },
    { id: "subject-civil", code: "CV301", name: "Civil Engineering" },
  ];
  await page.route("**/api/v1/configuration/catalog", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    await route.fulfill({ response, json: { ...body, subjects } });
  });
  let submitted: Record<string, unknown> | null = null;
  await page.route("**/api/v1/evaluator-management", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    submitted = route.request().postDataJSON();
    await route.fulfill({ json: { id: "evaluator-1", evaluator_code: "EV-NEW", status: "pending", version: 1, login_enabled: false, face_enrollment_required: false } });
  });
  await page.getByRole("navigation", { name: "Primary navigation" }).getByRole("button", { name: "Evaluator master" }).click();
  await page.getByRole("button", { name: "Register evaluator" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Search subjects").fill("Computer");
  await dialog.getByRole("checkbox", { name: /CS401/ }).check();
  await dialog.getByLabel("Search subjects").fill("Civil");
  await dialog.getByRole("checkbox", { name: /CV301/ }).check();
  await expect(dialog.locator('input[name="subject_ids"]')).toHaveCount(2);
  await dialog.locator('[name="evaluator_code"]').fill("EV-NEW");
  await dialog.locator('[name="display_name"]').fill("Dr. New Examiner");
  await dialog.locator('[name="email"]').fill("new@example.edu");
  await dialog.locator('[name="mobile"]').fill("9000000000");
  await dialog.locator('[name="employee_id"]').fill("EMP-NEW");
  await dialog.locator('[name="institution_name"]').fill("Northbridge University");
  await dialog.locator('[name="department"]').fill("Computer Science");
  await dialog.locator('[name="designation"]').fill("Professor");
  await dialog.locator('[name="qualification"]').fill("PhD");
  await dialog.locator('[name="years_experience"]').fill("10");
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => submitted).not.toBeNull();
  expect((submitted as unknown as { subject_ids: string[] }).subject_ids).toEqual(["subject-computer", "subject-civil"]);
  await page.unrouteAll({ behavior: "wait" });
});
