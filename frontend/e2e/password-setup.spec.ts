import { expect, test } from "@playwright/test";

test("password setup recovers from token errors and completes", async ({ page }) => {
  let csrfAvailable = false;
  let passwordChanged = false;
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({ json: {
    user: { id: 42, name: "New admin", email: "newadmin@example.test" },
    tenant: { id: "tenant-1", name: "Northbridge University", code: "NBU" },
    role: "university_admin", permissions: [], must_change_password: !passwordChanged,
    enabled_modules: [], tenants: [{ id: "tenant-1", name: "Northbridge University", role: "university_admin" }],
    session: { id: "session-1", timeout_minutes: 30 },
  } }));
  await page.route("**/api/v1/auth/csrf", (route) => route.fulfill(csrfAvailable
    ? { json: { csrf_token: "test-csrf-token" } }
    : { status: 428, json: { detail: "Complete password setup before continuing" } }));
  await page.route("**/api/v1/auth/password/complete-setup", (route) => {
    passwordChanged = true;
    return route.fulfill({ json: { ok: true } });
  });
  await page.route("**/api/v1/operations/overview", (route) => route.fulfill({ json: {
    generated_at: new Date().toISOString(), session: null, metrics: {}, pipeline: [], attention: [], module_counts: {}, papers: [], recent_events: [],
  } }));
  await page.route("**/api/v1/auth/notifications", (route) => route.fulfill({ json: { items: [], unread_count: 0 } }));

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Set your password" })).toBeVisible();
  await page.getByLabel("New password").fill("Unique-Password-2046!");
  await page.getByLabel("Confirm password").fill("Unique-Password-2046!");
  await page.getByRole("button", { name: "Set password and continue" }).click();
  await expect(page.getByText("Complete password setup before continuing")).toBeVisible();
  await expect(page.getByRole("button", { name: "Set password and continue" })).toBeEnabled();

  csrfAvailable = true;
  await page.getByRole("button", { name: "Set password and continue" }).click();
  await expect(page.getByRole("heading", { name: "Evaluation operations" })).toBeVisible();
});
