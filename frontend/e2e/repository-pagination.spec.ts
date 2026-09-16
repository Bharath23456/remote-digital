import { expect, test } from "@playwright/test";

test("repository pages assets and upload history without a direct upload path", async ({ page }) => {
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({ json: {
    user: { id: 1, name: "University admin", email: "admin@example.test" },
    tenant: { id: "tenant-1", name: "Northbridge University", code: "NBU" },
    role: "university_admin", permissions: [], must_change_password: false,
    enabled_modules: ["repository"], tenants: [{ id: "tenant-1", name: "Northbridge University", role: "university_admin" }],
    session: { id: "session-1", timeout_minutes: 30 },
  } }));
  await page.route("**/api/v1/operations/overview", (route) => route.fulfill({ json: {
    generated_at: new Date().toISOString(), session: null, metrics: {}, pipeline: [], attention: [], module_counts: {}, papers: [], recent_events: [],
  } }));
  await page.route("**/api/v1/auth/notifications", (route) => route.fulfill({ json: { items: [], unread_count: 0 } }));
  await page.route("**/api/v1/repository/catalog?**", (route) => {
    const params = new URL(route.request().url()).searchParams;
    const assetPage = Number(params.get("asset_page") || 1);
    const uploadPage = Number(params.get("upload_page") || 1);
    const asset = (index: number) => ({ id: `asset-${index}`, script_id: "script-1", script: `AS-${index}`, kind: "thumbnail", page_number: index, sha256: "a".repeat(64), byte_size: 1024, mime_type: "image/webp", version: 1, retention_until: "2033-12-31", legal_hold: false, integrity_checked_at: null, archived_at: null, backup_status: "completed", replication_status: "completed" });
    const upload = (index: number) => ({ id: `upload-${index}`, script: `AS-${index}`, kind: "raw_scan", page_number: index, asset_version: 1, status: "completed", expires_at: "2030-01-01T00:00:00Z", sha256: "a".repeat(64), byte_size: 1024, version: 1 });
    return route.fulfill({ json: {
      assets: assetPage === 1 ? Array.from({ length: 25 }, (_, index) => asset(index + 1)) : [asset(26)],
      uploads: uploadPage === 1 ? Array.from({ length: 25 }, (_, index) => upload(index + 1)) : [upload(26)],
      asset_pagination: { page: assetPage, page_size: 25, total: 26 }, upload_pagination: { page: uploadPage, page_size: 25, total: 26 }, summary: { total: 26, holds: 0, verified: 0 },
    } });
  });

  await page.goto("/");
  if ((page.viewportSize()?.width || 0) <= 760) await page.getByTitle("Open navigation").click();
  await page.getByRole("navigation", { name: "Primary navigation" }).getByRole("button", { name: "Script repository", exact: true }).click();
  await expect(page.getByRole("cell", { name: /AS-1/ }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Upload page" })).toHaveCount(0);
  await expect(page.getByText("Page 1 of 2")).toBeVisible();
  await page.getByRole("button", { name: "Next page" }).click();
  await expect(page.getByRole("cell", { name: /AS-26/ }).first()).toBeVisible();
  await page.getByRole("button", { name: "Upload history" }).click();
  await page.getByRole("button", { name: "Next page" }).click();
  await expect(page.getByText("Page 2 of 2")).toBeVisible();
  await expect(page.getByRole("cell", { name: "AS-26", exact: true })).toBeVisible();
});
