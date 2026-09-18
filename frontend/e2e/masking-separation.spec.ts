import { expect, test, type Page } from "@playwright/test";

async function openMaskingQueue(page: Page, currentUserId: number, status = "detected") {
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
    current_user_id: currentUserId, current_role: "university_admin", links: [], resolutions: [], scripts: [], jobs: [{
      id: "job-1", script_id: "script-1", script: "AS-UPLOAD-929A004B905B", profile: "university-standard-v1",
      status, page_count: 2, detection_confidence: 0.98, created_by_id: 1, reviewed_by_id: status === "applied" ? 2 : null, applied_by_id: status === "applied" ? 3 : null,
      verified_by_id: null, failure_reason: "", version: status === "applied" ? 4 : 1, regions: [{ id: "region-1", page_number: 1, category: "identity_page", x: 0, y: 0, width: 1, height: 1, source: "automatic", confidence: 1 }],
    }],
  } }));
  const image = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9WnT9lAAAAAASUVORK5CYII=", "base64");
  await page.route("**/api/v1/anonymisation/masking-jobs/job-1/preview/**", (route) => route.fulfill({ body: image, contentType: "image/png" }));
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
  await page.getByRole("button", { name: "Review" }).click();
  await expect(page.getByRole("dialog").getByAltText("Masked page 1 of AS-UPLOAD-929A004B905B")).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve masks" })).toBeDisabled();
  await page.getByRole("button", { name: "Next page" }).click();
  await expect(page.getByRole("button", { name: "Approve masks" })).toBeEnabled();
});

test("independent verifier can reject a generated copy for remasking", async ({ page }) => {
  await openMaskingQueue(page, 4, "applied");
  let rejection: { notes?: string } = {};
  await page.route("**/api/v1/anonymisation/masking-jobs/job-1/reject", async (route) => {
    rejection = route.request().postDataJSON();
    await route.fulfill({ json: { id: "job-1", status: "failed", version: 5 } });
  });
  await page.getByRole("button", { name: "Verify", exact: true }).click();
  await expect(page.getByText("Generated masked copy")).toBeVisible();
  await expect(page.getByRole("button", { name: "Reject for remasking" })).toBeDisabled();
  await page.getByRole("textbox", { name: "Decision note" }).fill("Candidate number is still visible");
  await page.getByRole("button", { name: "Reject for remasking" }).click();
  await expect(page.getByText("Script returned for remasking")).toBeVisible();
  expect(rejection.notes).toBe("Candidate number is still visible");
});
