import { expect, test } from "@playwright/test";

async function login(page: import("@playwright/test").Page, email: string) {
  await page.goto("/");
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password").fill("ChangeMe123!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: email.startsWith("platform") ? "University management" : "Evaluation operations" })).toBeVisible();
}

async function navigate(page: import("@playwright/test").Page, label: string) {
  await page.getByRole("navigation", { name: "Primary navigation" }).getByRole("button", { name: label, exact: true }).click();
}

test("access governance keeps privileged actions within the viewport", async ({ page }) => {
  await login(page, "admin@admiezo.local");
  if ((page.viewportSize()?.width || 0) <= 760) {
    await page.getByTitle("Open navigation").click();
    await page.getByRole("navigation", { name: "Primary navigation" }).getByRole("button", { name: "Access governance", exact: true }).evaluate((button: HTMLButtonElement) => button.click());
  } else {
    await navigate(page, "Access governance");
  }
  await page.getByRole("button", { name: "Access", exact: true }).click();
  await expect(page.getByText("Privileged access", { exact: true })).toBeVisible();
  const dimensions = await page.evaluate(() => ({
    viewport: window.innerWidth,
    document: document.documentElement.scrollWidth,
    toolbarRight: [...document.querySelectorAll(".workspace-toolbar")].at(-1)?.getBoundingClientRect().right || 0,
  }));
  expect(dimensions.document).toBeLessThanOrEqual(dimensions.viewport + 1);
  expect(dimensions.toolbarRight).toBeLessThanOrEqual(dimensions.viewport + 1);
});

test("university admin creates a module-scoped evaluator login", async ({ page }) => {
  await login(page, "admin@admiezo.local");
  await navigate(page, "Access governance");
  await page.getByRole("button", { name: "Access" }).click();
  await page.getByRole("button", { name: "Re-authenticate" }).click();
  await page.getByLabel("Password").fill("ChangeMe123!");
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page.getByText("Step-up active")).toBeVisible();
  await page.getByRole("button", { name: "Access", exact: true }).click();
  await page.getByRole("button", { name: "Add user" }).click();
  const email = `playwright.evaluator.${Date.now()}@example.edu`;
  await page.getByLabel("First name").fill("Playwright");
  await page.getByLabel("Last name").fill("Evaluator");
  await page.getByLabel("Institutional email").fill(email);
  await page.getByLabel("Role").selectOption("evaluator");
  await page.getByRole("checkbox", { name: "Evaluation", exact: true }).check();
  await page.getByRole("button", { name: "Create user" }).click();
  const credentialDialog = page.getByRole("dialog");
  await expect(credentialDialog.getByRole("heading", { name: "User login ready" })).toBeVisible();
  await expect(credentialDialog.getByText(email, { exact: true })).toBeVisible();
  await expect(credentialDialog.getByText("Temporary password", { exact: true })).toBeVisible();
});

test("verification evidence uploads through real signed storage", async ({ page }) => {
  await login(page, "admin@admiezo.local");
  await navigate(page, "Evaluator master");
  await page.getByRole("button", { name: "Verification", exact: true }).click();
  const draft = page.locator("tbody tr").filter({ has: page.getByText("Draft", { exact: true }) }).first();
  await expect(draft).toBeVisible();
  await draft.getByTitle("Upload evidence").click();
  await page.getByLabel("PDF or image").setInputFiles({ name: "identity.png", mimeType: "image/png", buffer: Buffer.from("real signed upload") });
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Verification document encrypted and hash-verified", { exact: true })).toBeVisible();
  await expect(draft.getByText(/verified/)).toBeVisible();
});

test("administrator completes evaluator checks before submission", async ({ page }) => {
  await login(page, "admin@admiezo.local");
  await navigate(page, "Evaluator master");
  await page.getByRole("button", { name: "Verification", exact: true }).click();
  const draft = page.locator("tbody tr").filter({ has: page.getByText("Draft", { exact: true }) }).first();
  await draft.getByTitle("Complete verification checks").click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("heading", { name: "Complete verification checks" })).toBeVisible();
  const checkboxes = await dialog.getByRole("checkbox").all();
  expect(checkboxes).toHaveLength(14);
  for (const checkbox of checkboxes) {
    if (!(await checkbox.isChecked())) await checkbox.check();
  }
  await dialog.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Verification checklist updated", { exact: true })).toBeVisible();
  await expect(draft.getByText("14/14", { exact: true })).toBeVisible();
  await expect(draft.getByRole("button", { name: "Submit", exact: true })).toBeEnabled();
});

test("active authenticator can be replaced after step-up", async ({ page }) => {
  await login(page, "admin@admiezo.local");
  await navigate(page, "Access governance");
  await page.getByRole("button", { name: "Replace authenticator" }).click();
  const stepUp = page.getByRole("dialog");
  await expect(stepUp.getByRole("heading", { name: "Re-authenticate" })).toBeVisible();
  await stepUp.getByLabel("Password").fill("ChangeMe123!");
  await stepUp.getByRole("button", { name: "Verify" }).click();
  await expect(page.getByText("Privileged actions unlocked for this session", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Replace authenticator" }).click();
  const setup = page.getByRole("dialog");
  await expect(setup.getByRole("heading", { name: "Replace authenticator" })).toBeVisible();
  await expect(setup.getByText("Your current method remains active until confirmation.", { exact: false })).toBeVisible();
  await expect(setup.getByRole("img")).toBeVisible();
  await setup.getByTitle("Close").click();
});

test("evaluator registration issues a linked Evaluation login", async ({ page }) => {
  await login(page, "admin@admiezo.local");
  await navigate(page, "Evaluator master");
  await page.getByRole("button", { name: "Register evaluator" }).click();
  const suffix = Date.now();
  const email = `registered.evaluator.${suffix}@example.edu`;
  await page.getByLabel("Evaluator ID").fill(`PW-${suffix}`);
  await page.getByLabel("Full name").fill("Registered Evaluator");
  await page.getByLabel("Institutional email").fill(email);
  await page.getByLabel("Mobile").fill("+919900001234");
  await page.getByLabel("Employee ID").fill(`EMP-${suffix}`);
  await page.getByLabel("Institution", { exact: true }).fill("Northbridge University");
  await page.getByLabel("Department").fill("Computer Science");
  await page.getByLabel("Designation").fill("Assistant Professor");
  await page.getByLabel("Qualification").fill("PhD");
  await page.getByLabel("Experience in years").fill("6");
  await expect(page.getByRole("checkbox", { name: /Create Evaluation module login/ })).toBeChecked();
  await page.getByRole("button", { name: "Save" }).click();
  const credentialDialog = page.getByRole("dialog");
  await expect(credentialDialog.getByRole("heading", { name: "Evaluator login ready" })).toBeVisible();
  await expect(credentialDialog.getByText(email, { exact: true })).toBeVisible();
  await expect(credentialDialog.getByText("Temporary password", { exact: true })).toBeVisible();
});

test("platform admin configures a university-specific form field", async ({ page }) => {
  await login(page, "platform@admiezo.local");
  await page.getByTitle("Configure custom form fields").first().click();
  await expect(page.getByRole("heading", { name: /Form fields/ })).toBeVisible();
  const key = `pw_field_${Date.now()}`;
  await page.getByLabel("Form").selectOption("institution");
  await page.getByLabel("Field key").fill(key);
  await page.getByLabel("Label").fill("Playwright campus code");
  await page.getByRole("button", { name: "Add field" }).click();
  await expect(page.locator(".custom-field-list").getByText("Playwright campus code", { exact: true }).last()).toBeVisible();
  const fieldDialog = page.getByRole("dialog");
  await fieldDialog.getByTitle("Close").click();
  await expect(fieldDialog).toBeHidden();
  await page.getByTitle("Sign out").click();
  await page.getByLabel("Email address").fill("admin@admiezo.local");
  await page.getByLabel("Password").fill("ChangeMe123!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Evaluation operations" })).toBeVisible();
  await navigate(page, "Enterprise settings");
  await page.getByRole("button", { name: "Add institution" }).click();
  const dialog = page.getByRole("dialog");
  const customField = dialog.locator(`[name="custom_${key}"]`);
  await expect(customField).toBeVisible();
  const suffix = Date.now();
  await dialog.getByLabel("Name").fill(`Playwright Campus ${suffix}`);
  await dialog.getByLabel("Code", { exact: true }).fill(`pw-campus-${suffix}`);
  await dialog.getByLabel("Parent").selectOption({ label: "Northbridge University · university" });
  await customField.fill(`CAMP-${suffix}`);
  await dialog.getByRole("button", { name: "Add institution" }).click();
  await expect(page.getByText("Institution added to the isolated university hierarchy", { exact: true })).toBeVisible();
});

test("evaluator receives and opens an in-app assignment notification", async ({ page }) => {
  await login(page, "admin@admiezo.local");
  const result = await page.evaluate(async () => {
    const catalog = await (await fetch("/api/v1/phase4/catalog?section=operations")).json();
    const recipient = catalog.references.users.find((user: { email: string }) => user.email === "evaluator1043@admiezo.local");
    const csrf = await (await fetch("/api/v1/auth/csrf")).json();
    const response = await fetch("/api/v1/phase4/notifications", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf.csrf_token }, body: JSON.stringify({ user_id: Number(recipient.id), category: "assignment", title: "New evaluation assigned", body: "A new anonymous script is ready for evaluation.", severity: "normal", channels: ["in_app"], mandatory_acknowledgement: false }) });
    return response.status;
  });
  expect(result).toBe(200);
  await page.getByTitle("Sign out").click();
  await page.getByLabel("Email address").fill("evaluator1043@admiezo.local");
  await page.getByLabel("Password").fill("ChangeMe123!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Evaluation desk" })).toBeVisible();
  await page.getByTitle("Notifications").click();
  const notification = page.getByText("New evaluation assigned", { exact: true }).first();
  await expect(notification).toBeVisible();
  await notification.click();
  await expect(page.getByRole("heading", { name: "Evaluation desk" })).toBeVisible();
});
