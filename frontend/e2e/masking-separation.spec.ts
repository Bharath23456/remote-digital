import { expect, test, type Page } from "@playwright/test";

async function openMaskingQueue(page: Page, currentUserId: number) {
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({ json: {
    user: { id: currentUserId, name: "Masking operator", email: "operator@example.test" },
    tenant: { id: "test-tenant", name: "Northbridge University", code: "NBU" },
    role: "university_admin", permissions: [], must_change_password: false,
    enabled_modules: ["anonymisation"], tenants: [{ id: "test-tenant", name: "Northbridge University", role: "university_admin" }],
    session: { id: "test-session", timeout_minutes: 30 },
  } }));
  await page.route("**/api/v1/operations/overview", (route) => route.fulfill({ json: {
    generated_at: new Date().toISOString(), session: null, metrics: {}, pipeline: [], attention: [], module_counts: {}, papers: [], recent_events: [],
  } }));
  await page.route("**/api/v1/auth/notifications", (route) => route.fulfill({ json: { items: [], unread_count: 0 } }));
  await page.route("**/api/v1/anonymisation/catalog", (route) => route.fulfill({ json: {
    current_user_id: currentUserId, links: [], resolutions: [], scripts: [], jobs: [{
      id: "job-1", script_id: "script-1", script: "AS-UPLOAD-929A004B905B", profile: "university-standard-v1",
      status: "detected", detection_confidence: 0.98, created_by_id: 1, reviewed_by_id: null, applied_by_id: null,
      verified_by_id: null, failure_reason: "", version: 1, regions: [{ id: "region-1", page_number: 1, category: "candidate_name", x: 0, y: 0, width: 0.2, height: 0.1, source: "automatic", confidence: 0.98 }],
    }],
  } }));
  await page.goto("/");
  if ((page.viewportSize()?.width || 0) <= 760) await page.getByTitle("Open navigation").click();
  await page.getByRole("navigation", { name: "Primary navigation" }).getByRole("button", { name: "Anonymization", exact: true }).click();
  await expect(page.getByText("AS-UPLOAD-929A004B905B")).toBeVisible();
}

test("mask creator sees why Review is unavailable", async ({ page }) => {
  await openMaskingQueue(page, 1);
  await expect(page.getByRole("button", { name: "Review" })).toBeDisabled();
  await expect(page.getByText("You detected this script. Another authorized user must review it.")).toBeVisible();
});

test("independent operator can review a detected job", async ({ page }) => {
  await openMaskingQueue(page, 2);
  await expect(page.getByRole("button", { name: "Review" })).toBeEnabled();
  await expect(page.getByText("You detected this script. Another authorized user must review it.")).toHaveCount(0);
});
