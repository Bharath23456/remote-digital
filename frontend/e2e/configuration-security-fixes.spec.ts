import { expect, test, type Page } from "@playwright/test";

async function stubShell(page: Page, module: string) {
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({ json: {
    user: { id: 1, name: "University admin", email: "admin@example.test" },
    tenant: { id: "tenant-1", name: "Northbridge University", code: "NBU" },
    role: "university_admin", permissions: [], must_change_password: false,
    enabled_modules: [module], tenants: [{ id: "tenant-1", name: "Northbridge University", role: "university_admin" }],
    session: { id: "session-1", timeout_minutes: 30 },
  } }));
  await page.route("**/api/v1/operations/overview", (route) => route.fulfill({ json: {
    generated_at: new Date().toISOString(), session: null, metrics: {}, pipeline: [], attention: [], module_counts: {}, papers: [], recent_events: [],
  } }));
  await page.route("**/api/v1/auth/notifications", (route) => route.fulfill({ json: { items: [], unread_count: 0 } }));
  await page.goto("/");
  if ((page.viewportSize()?.width || 0) <= 760) await page.getByTitle("Open navigation").click();
}

test("legal hold confirms identity before changing protection", async ({ page }) => {
  const asset = { id: "asset-1", script_id: "script-1", script: "AS-101", kind: "evaluation", page_number: 1, sha256: "a".repeat(64), byte_size: 1024, mime_type: "image/webp", version: 1, retention_until: "2033-12-31", legal_hold: false, integrity_checked_at: null, archived_at: null, backup_status: "completed", replication_status: "completed" };
  await page.route("**/api/v1/repository/catalog?**", (route) => route.fulfill({ json: { assets: [asset], uploads: [], asset_pagination: { page: 1, page_size: 25, total: 1 }, upload_pagination: { page: 1, page_size: 25, total: 0 }, summary: { total: 1, holds: 0, verified: 0 } } }));
  const calls: string[] = [];
  await page.route("**/api/v1/auth/step-up", (route) => { calls.push("step-up"); return route.fulfill({ json: { step_up_expires_at: new Date().toISOString() } }); });
  await page.route("**/api/v1/repository/assets/asset-1/legal-hold", (route) => { calls.push("legal-hold"); return route.fulfill({ json: { id: "asset-1", legal_hold: true } }); });
  await stubShell(page, "repository");
  await page.getByRole("navigation", { name: "Primary navigation" }).getByRole("button", { name: "Script repository", exact: true }).click();
  await page.getByRole("button", { name: "Legal hold and retention" }).click();
  await page.getByRole("dialog").getByRole("checkbox", { name: "Legal hold enabled" }).check();
  await page.getByRole("dialog").getByRole("textbox", { name: "Password or authenticator code" }).fill("ChangeMe123!");
  await page.getByRole("button", { name: "Update protection" }).click();
  await expect(page.getByText("Legal hold settings updated")).toBeVisible();
  expect(calls).toEqual(["step-up", "legal-hold"]);
});

test("configuration exposes linked terms and actionable readiness blockers", async ({ page }) => {
  await page.route("**/api/v1/configuration/catalog", (route) => route.fulfill({ json: {
    academic_years: [{ id: "year-1", label: "2026-27", starts_on: "2026-07-01", ends_on: "2027-06-30", version: 1, is_active: true }],
    terms: [{ id: "term-1", academic_year_id: "year-1", academic_year: "2026-27", name: "Odd semester", starts_on: "2026-07-01", ends_on: "2026-12-31", version: 1, is_active: true }],
    regulations: [{ id: "reg-1", code: "R-2025", title: "Regulation 2025", is_active: true, version: 1 }],
    papers: [], sessions: [], events: [], programmes: [], courses: [], subjects: [], centres: [], change_requests: [],
  } }));
  await page.route("**/api/v1/configuration/readiness", (route) => route.fulfill({ json: { decision: "not_ready", ready: false, session: null, paper_count: 0, issues: [], critical_alerts: ["No active evaluation event is scheduled"] } }));
  await page.route("**/api/v1/enterprise/form-fields?*", (route) => route.fulfill({ json: { fields: [] } }));
  await stubShell(page, "configuration");
  await page.getByRole("navigation", { name: "Primary navigation" }).getByRole("button", { name: "Exam configuration", exact: true }).click();
  await expect(page.getByRole("button", { name: /No active evaluation event is scheduled/ })).toBeVisible();
  await page.getByRole("button", { name: "Exam sessions" }).click();
  await page.getByRole("button", { name: "Add Exam session" }).click();
  await expect(page.getByRole("dialog").getByRole("combobox", { name: "Term" })).toContainText("Odd semester");
  await page.getByRole("dialog").getByRole("button", { name: "Close" }).click();
  await page.getByRole("button", { name: "Academic years" }).click();
  await page.getByRole("button", { name: "Overlap exception" }).click();
  await expect(page.getByRole("dialog").getByRole("heading", { name: "Request overlap exception" })).toBeVisible();
});
