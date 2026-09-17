import { expect, test } from "@playwright/test";

test("platform audit navigation survives refresh", async ({ page }) => {
  let auditLoads = 0;
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({ json: {
    user: { id: 10, name: "Platform admin", email: "platform@example.test" },
    tenant: { id: "platform", name: "ADMIEZO", code: "PLATFORM" },
    role: "platform_admin", permissions: [], must_change_password: false,
    enabled_modules: [], tenants: [], session: { id: "platform-session", timeout_minutes: 30 },
  } }));
  await page.route("**/api/v1/enterprise/control-plane", (route) => route.fulfill({ json: {
    summary: { universities: 0, active: 0, domains: 0, administrators: 0 }, universities: [],
  } }));
  await page.route("**/api/v1/enterprise/control-plane/audit?*", (route) => {
    auditLoads += 1;
    return route.fulfill({ json: [] });
  });

  await page.goto("/");
  if ((page.viewportSize()?.width || 0) <= 760) await page.getByTitle("Open navigation").click();
  await page.getByRole("navigation", { name: "Primary navigation" }).getByRole("button", { name: "Platform audit" }).click();
  await expect(page.getByRole("heading", { name: "Cross-university events" })).toBeVisible();
  await expect(page).toHaveURL(/#audit$/);
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect.poll(() => auditLoads).toBeGreaterThan(1);
  await expect(page.getByRole("heading", { name: "Cross-university events" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Cross-university events" })).toBeVisible();
});
