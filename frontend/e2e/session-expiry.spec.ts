import { expect, test, type Page } from "@playwright/test";

async function openAuthenticatedWorkspace(page: Page) {
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({ json: {
    user: { id: 1045, name: "Test Evaluator", email: "evaluator1045@admiezo.local" },
    tenant: { id: "test-tenant", name: "Northbridge University", code: "NBU" },
    role: "evaluator", permissions: [], must_change_password: false,
    enabled_modules: ["evaluation"], tenants: [{ id: "test-tenant", name: "Northbridge University", role: "evaluator" }],
    session: { id: "test-session", timeout_minutes: 5 },
  } }));
  await page.route("**/api/v1/allocation/catalog", (route) => route.fulfill({ json: { assignments: [] } }));
  await page.route("**/api/v1/auth/notifications", (route) => route.fulfill({ json: { items: [], unread_count: 0 } }));
  await page.route("**/api/v1/auth/logout", (route) => route.fulfill({ json: { ok: true } }));
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Account settings" })).toBeVisible();
}

test("inactivity returns the app to sign-in across reload", async ({ page }) => {
  await openAuthenticatedWorkspace(page);
  await page.evaluate(() => sessionStorage.setItem("admiezo-last-activity-test-session", String(Date.now() - 6 * 60_000)));
  await page.reload();
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
  await expect(page.getByText("Your session expired due to inactivity. Sign in again.")).toBeVisible();
});

test("an open workspace signs out when the idle timer elapses", async ({ page }) => {
  await page.clock.install();
  await openAuthenticatedWorkspace(page);
  await page.clock.fastForward(5 * 60_000 + 1000);
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
  await expect(page.getByText("Your session expired due to inactivity. Sign in again.")).toBeVisible();
});

test("401 from a protected request returns the app to sign-in", async ({ page }) => {
  await openAuthenticatedWorkspace(page);
  await page.route("**/api/v1/auth/security-context", (route) => route.fulfill({ status: 401, json: { detail: "Session expired" } }));
  await page.getByRole("button", { name: "Account settings" }).click();
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
  await expect(page.getByText("Your session has ended. Sign in again.")).toBeVisible();
});
