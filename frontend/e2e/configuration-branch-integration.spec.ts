import { expect, test } from "@playwright/test";

test("paper configuration shows question details and new question fields", async ({ page }) => {
  const paper = {
    id: "paper-1", session_id: "session-1", subject_id: "subject-1", code: "PHY-101",
    title: "Physics", max_marks: "100.00", pass_marks: "40.00", valuation_rounds: 2,
    discrepancy_threshold: "15.00", moderation_required: false, rules: {}, status: "draft",
    version: 2, readiness: { ready: true, issues: [] }, questions: [{ id: "question-1", number: "Q1", sub_question: "a", max_marks: "100.00", question_type: "descriptive", required: true, position: 1 }],
  };
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({ json: {
    user: { id: 1, name: "University admin", email: "admin@example.test" },
    tenant: { id: "tenant-1", name: "Northbridge University", code: "NBU" },
    role: "university_admin", permissions: [], must_change_password: false,
    enabled_modules: ["configuration"], tenants: [{ id: "tenant-1", name: "Northbridge University", role: "university_admin" }],
    session: { id: "session-1", timeout_minutes: 30 },
  } }));
  await page.route("**/api/v1/operations/overview", (route) => route.fulfill({ json: {
    generated_at: new Date().toISOString(), session: null, metrics: {}, pipeline: [], attention: [], module_counts: {}, papers: [], recent_events: [],
  } }));
  await page.route("**/api/v1/configuration/catalog", (route) => route.fulfill({ json: {
    papers: [paper], subjects: [{ id: "subject-1", code: "PHY", name: "Physics" }],
  } }));
  await page.route("**/api/v1/configuration/readiness", (route) => route.fulfill({ json: {
    decision: "not_ready", ready: false, session: null, paper_count: 1, issues: [], critical_alerts: [],
  } }));
  await page.route("**/api/v1/auth/notifications", (route) => route.fulfill({ json: { items: [], unread_count: 0 } }));
  await page.route("**/api/v1/enterprise/form-fields?*", (route) => route.fulfill({ json: { fields: [] } }));

  await page.goto("/");
  if ((page.viewportSize()?.width || 0) <= 760) await page.getByTitle("Open navigation").click();
  await page.getByRole("navigation", { name: "Primary navigation" }).getByRole("button", { name: "Exam configuration", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Physics" })).toBeVisible();
  await page.getByRole("button", { name: "1 question configured" }).click();
  await expect(page.getByText("Question Q1 (a)")).toBeVisible();
  await page.getByRole("button", { name: "Questions & marks" }).click();
  await expect(page.getByText("Q. Q1 (a)")).toBeVisible();
  await page.getByRole("button", { name: "Close" }).click();
  await page.getByRole("button", { name: "Add question" }).click();
  await expect(page.getByRole("textbox", { name: "Sub-question" })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Question type" })).toBeVisible();
});
