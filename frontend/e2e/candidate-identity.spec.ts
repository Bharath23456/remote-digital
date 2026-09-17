import { expect, test } from "@playwright/test";

test("admin selects institutions and stores optional cropped and uploaded identity images", async ({ page }) => {
  const universityId = "00000000-0000-0000-0000-000000000101";
  const collegeId = "00000000-0000-0000-0000-000000000102";
  const scriptId = "00000000-0000-0000-0000-000000000201";
  let authorizedCollege = "";
  let sentImages = false;
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({ json: {
    user: { id: 1, name: "University admin", email: "admin@example.test" },
    tenant: { id: "tenant-1", name: "Northbridge University", code: "NBU" },
    role: "university_admin", permissions: [], must_change_password: false,
    enabled_modules: ["anonymisation"], tenants: [{ id: "tenant-1", name: "Northbridge University", role: "university_admin" }],
    session: { id: "session-1", timeout_minutes: 30 },
  } }));
  await page.route("**/api/v1/operations/overview", (route) => route.fulfill({ json: {
    generated_at: new Date().toISOString(), session: null, metrics: {}, pipeline: [], attention: [], module_counts: {}, papers: [], recent_events: [],
  } }));
  await page.route("**/api/v1/auth/notifications", (route) => route.fulfill({ json: { items: [], unread_count: 0 } }));
  await page.route("**/api/v1/auth/csrf", (route) => route.fulfill({ json: { csrf_token: "test-csrf" } }));
  await page.route("**/api/v1/auth/step-up", (route) => route.fulfill({ json: { step_up_expires_at: "2030-01-01T00:00:00Z" } }));
  await page.route("**/api/v1/anonymisation/catalog", (route) => route.fulfill({ json: {
    jobs: [], links: [], resolutions: [], current_user_id: 1, current_role: "university_admin",
    scripts: [{ id: scriptId, script_code: "AS-IDENTITY-201", state: "scanned", page_count: 10, version: 2 }],
    institutions: [{ id: universityId, name: "Northbridge University", code: "northbridge", kind: "university", parent_id: null }],
  } }));
  await page.route("**/api/v1/enterprise/institutions", (route) => route.fulfill({ json: { id: collegeId, version: 1 } }));
  const firstPage = `data:image/svg+xml,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="600" height="400"><rect width="600" height="400" fill="white"/><text x="50" y="80" font-size="25">Candidate signature</text><rect x="50" y="120" width="150" height="90" fill="gray"/></svg>')}`;
  await page.route(`**/api/v1/anonymisation/scripts/${scriptId}/identity-page`, (route) => route.fulfill({ json: { url: firstPage, mime_type: "image/png" } }));
  await page.route(`**/api/v1/anonymisation/scripts/${scriptId}/identity/authorize`, (route) => {
    authorizedCollege = JSON.parse(route.request().postData() || "{}").college_id;
    return route.fulfill({ json: { endpoint: "/identity-api/v1/candidates", token: "test-token", link_id: "link-1", version: 1 } });
  });
  await page.route("**/identity-api/v1/candidates", (route) => {
    const body = route.request().postData() || "";
    sentImages = body.includes('name="signature_image"') && body.includes('name="photo_image"');
    return route.fulfill({ status: 201, json: { receipt: "test-receipt", stored: true } });
  });
  await page.route("**/api/v1/anonymisation/identity-links/link-1/confirm", (route) => route.fulfill({ json: { stored: true } }));

  await page.goto("/");
  if ((page.viewportSize()?.width || 0) <= 760) await page.getByTitle("Open navigation").click();
  await page.getByRole("navigation", { name: "Primary navigation" }).getByRole("button", { name: "Anonymization", exact: true }).click();
  await page.getByRole("button", { name: "Register identity" }).click();
  await page.getByLabel("Anonymous script").selectOption(scriptId);
  await page.getByRole("button", { name: "Add college" }).click();
  await page.getByRole("textbox", { name: "Name", exact: true }).fill("Engineering College");
  await page.getByRole("textbox", { name: "Code", exact: true }).fill("engineering-college");
  await page.getByRole("button", { name: "Add college", exact: true }).last().click();
  await expect(page.getByLabel("College")).toHaveValue(collegeId);

  await page.getByLabel("Password", { exact: true }).fill("ChangeMe123!");
  await page.getByRole("button", { name: "Crop page 1" }).first().click();
  const hitbox = page.locator(".identity-crop-hitbox");
  await expect(hitbox).toBeVisible();
  const box = await hitbox.boundingBox();
  expect(box).not.toBeNull();
  await page.mouse.move(box!.x + box!.width * 0.08, box!.y + box!.height * 0.06);
  await page.mouse.down();
  await page.mouse.move(box!.x + box!.width * 0.55, box!.y + box!.height * 0.35);
  await page.mouse.up();
  await page.getByRole("button", { name: "Use crop" }).click();
  await expect(page.getByAltText("Signature selection")).toBeVisible();
  await page.getByLabel("Upload photo").setInputFiles({ name: "photo.png", mimeType: "image/png", buffer: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=", "base64") });
  await expect(page.getByAltText("Photo selection")).toBeVisible();
  await page.getByRole("textbox", { name: "Candidate name" }).fill("Maya Joseph");
  await page.getByRole("textbox", { name: "Register number" }).fill("REG-20401");
  await page.getByRole("button", { name: "Encrypt identity" }).click();
  await expect(page.getByText("Candidate identity encrypted in the isolated identity service")).toBeVisible();
  expect(authorizedCollege).toBe(collegeId);
  expect(sentImages).toBe(true);
});
