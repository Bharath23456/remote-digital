import { expect, test } from "@playwright/test";

test("scanning shows completed manual uploads with pages and pagination", async ({ page }) => {
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({ json: {
    user: { id: 1, name: "University admin", email: "admin@example.test" },
    tenant: { id: "tenant-1", name: "Northbridge University", code: "NBU" },
    role: "university_admin", permissions: [], must_change_password: false,
    enabled_modules: ["digitization"], tenants: [{ id: "tenant-1", name: "Northbridge University", role: "university_admin" }],
    session: { id: "session-1", timeout_minutes: 30 },
  } }));
  await page.route("**/api/v1/operations/overview", (route) => route.fulfill({ json: {
    generated_at: new Date().toISOString(), session: null, metrics: {}, pipeline: [], attention: [], module_counts: {}, papers: [], recent_events: [],
  } }));
  await page.route("**/api/v1/auth/notifications", (route) => route.fulfill({ json: { items: [], unread_count: 0 } }));
  await page.route("**/api/v1/scanning/catalog", (route) => route.fulfill({ json: { papers: [], eligible_scripts: [], scanners: [], batches: [], jobs: [], maintenance: [] } }));
  await page.route("**/api/v1/scan-processing/catalog", (route) => route.fulfill({ json: { profiles: [], runs: [], exceptions: [] } }));
  await page.route("**/api/v1/integrity/catalog", (route) => route.fulfill({ json: { unsigned_assets: [], manifests: [], checks: [], alerts: [] } }));
  await page.route("**/api/v1/repository/manual-scan/catalog?**", (route) => {
    const requestedPage = Number(new URL(route.request().url()).searchParams.get("page") || 1);
    const makeItem = (number: number) => ({ script_id: `script-${number}`, script: `AS-UPLOAD-${number}`, paper: "CS402-A", state: "scanned", page_count: 3, declared_pages: 3, pages: [1, 2, 3], uploaded_at: "2026-09-16T15:30:00Z" });
    return route.fulfill({ json: { items: requestedPage === 1 ? Array.from({ length: 20 }, (_, index) => makeItem(index + 1)) : [makeItem(21)], page: requestedPage, page_size: 20, total: 21 } });
  });

  await page.goto("/");
  if ((page.viewportSize()?.width || 0) <= 760) await page.getByTitle("Open navigation").click();
  await page.getByRole("navigation", { name: "Primary navigation" }).getByRole("button", { name: "Digitization", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Uploaded answer papers" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "AS-UPLOAD-1", exact: true })).toBeVisible();
  await expect(page.getByRole("cell", { name: /3 \/ 3.*Pages 1, 2, 3/ }).first()).toBeVisible();
  await page.getByRole("button", { name: "Next page" }).click();
  await expect(page.getByRole("cell", { name: "AS-UPLOAD-21", exact: true })).toBeVisible();
  await expect(page.getByText("Page 2 of 2")).toBeVisible();
});
