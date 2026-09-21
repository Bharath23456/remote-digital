import { expect, test } from "@playwright/test";

test("existing evaluator subjects are managed without adding duplicates", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Email address").fill("admin@admiezo.local");
  await page.getByLabel("Password").fill("ChangeMe123!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Evaluation operations" })).toBeVisible();

  const expertise = { id: "expertise-cs", subject_id: "subject-cs", subject_code: "CS401", subject_name: "Computer Science", level: 3, years_experience: 10, verified: false };
  await page.route("**/api/v1/evaluator-management", (route) => {
    if (route.request().method() !== "GET") return route.continue();
    return route.fulfill({ json: [{
      id: "evaluator-1", evaluator_code: "EV-NEW", display_name: "Dr. Subject Examiner",
      email: "examiner@example.edu", institution_name: "Northbridge University", department: "Computer Science",
      grade: "evaluator", status: "active", daily_capacity: 20, years_experience: 10,
      expertise: [expertise], face_status: "not_enrolled", face_enrolled: false,
    }] });
  });
  await page.route("**/api/v1/eligibility", (route) => route.fulfill({ json: { verifications: [], eligibility: [], history: [] } }));
  await page.route("**/api/v1/configuration/catalog", (route) => route.fulfill({ json: { subjects: [
    { id: "subject-cs", code: "CS401", name: "Computer Science" },
    { id: "subject-civil", code: "CV301", name: "Civil Engineering" },
  ] } }));
  await page.route("**/api/v1/enterprise/form-fields?**", (route) => route.fulfill({ json: { fields: [] } }));
  let verified = false;
  await page.route("**/api/v1/eligibility/expertise/expertise-cs/verify", (route) => {
    verified = true;
    return route.fulfill({ json: { id: "expertise-cs", verified: true } });
  });

  await page.getByRole("navigation", { name: "Primary navigation" }).getByRole("button", { name: "Evaluator master" }).click();
  const row = page.getByRole("row", { name: /Dr\. Subject Examiner/ });
  await row.locator("details.row-menu summary").click();
  await row.getByRole("button", { name: "Subjects" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("heading", { name: "Manage subject expertise" })).toBeVisible();
  await expect(dialog.getByText("CS401 · Computer Science")).toBeVisible();
  await expect(dialog.getByLabel("Add another subject").locator("option")).toHaveCount(1);
  await expect(dialog.getByLabel("Add another subject").locator("option")).toHaveValue("subject-civil");
  await dialog.getByRole("button", { name: "Verify" }).click();
  await expect.poll(() => verified).toBe(true);
  await page.unrouteAll({ behavior: "wait" });
});
