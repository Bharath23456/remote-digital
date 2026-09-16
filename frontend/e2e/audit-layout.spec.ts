import { expect, test } from "@playwright/test";

test("university audit records use the full content width", async ({ page }) => {
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({ json: {
    user: { id: 1, name: "University admin", email: "admin@example.test" },
    tenant: { id: "tenant-1", name: "Northbridge University", code: "NBU" },
    role: "university_admin", permissions: [], must_change_password: false,
    enabled_modules: ["audit"], tenants: [{ id: "tenant-1", name: "Northbridge University", role: "university_admin" }],
    session: { id: "session-1", timeout_minutes: 30 },
  } }));
  await page.route("**/api/v1/operations/overview", (route) => route.fulfill({ json: {
    generated_at: new Date().toISOString(), session: null, metrics: {}, pipeline: [], attention: [], module_counts: {}, papers: [], recent_events: [],
  } }));
  await page.route("**/api/v1/auth/notifications", (route) => route.fulfill({ json: { items: [], unread_count: 0 } }));
  await page.route("**/api/v1/audit/events", (route) => route.fulfill({ json: [{
    id: "event-1", user_name: "System", action_performed: "Integrity check passed",
    date: "2026-09-16", time: "14:12:56", ip_address: "Not recorded",
    script_id: "AS-123", details: "Script integrity verified",
  }] }));

  await page.goto("/");
  if ((page.viewportSize()?.width || 0) <= 760) await page.getByTitle("Open navigation").click();
  await page.getByRole("navigation", { name: "Primary navigation" }).getByRole("button", { name: "Audit trail", exact: true }).click();
  await expect(page.getByText("Integrity Check Passed")).toBeVisible();
  await expect(page.getByText("1 record", { exact: true })).toBeVisible();
  await expect(page.locator(".module-summary")).toHaveCount(0);
  const dimensions = await page.locator(".audit-workspace").evaluate((grid) => ({
    grid: grid.getBoundingClientRect().width,
    panel: grid.querySelector(".panel")?.getBoundingClientRect().width || 0,
  }));
  expect(dimensions.panel).toBeGreaterThan(dimensions.grid - 2);
});
