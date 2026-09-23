import { expect, test } from "@playwright/test";

async function navigate(page: import("@playwright/test").Page, label: string) {
  const mobile = (page.viewportSize()?.width || 1280) < 900;
  if (mobile) {
    await page.getByTitle("Open navigation").click();
    await expect(page.getByRole("navigation", { name: "Primary navigation" })).toBeVisible();
  }
  const button = page.getByRole("navigation", { name: "Primary navigation" }).getByRole("button", { name: label, exact: true });
  await button.scrollIntoViewIfNeeded();
  if (mobile) {
    await button.focus();
    await button.press("Enter");
  } else {
    await button.click();
  }
  if (mobile) await expect(page.getByRole("navigation", { name: "Primary navigation" })).toBeHidden();
}

async function login(page: import("@playwright/test").Page, email = "controller@admiezo.local") {
  await page.goto("/");
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password").fill("ChangeMe123!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  const heading = email.startsWith("evaluator") ? "Evaluation desk" : email.startsWith("platform") ? "University management" : "Evaluation operations";
  await expect(page.getByRole("heading", { name: heading })).toBeVisible();
}

test("required MFA guides a new user through authenticator enrollment", async ({ page }) => {
  let enrollmentPayload: { method_id?: string; code?: string } = {};
  await page.route("**/api/v1/auth/login", async (route) => {
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({
        challenge: "mfa_enrollment",
        enrollment: {
          method_id: "2f64a360-6454-414c-8222-5e95fe4da671",
          secret: "JBSWY3DPEHPK3PXP",
          provisioning_uri: "otpauth://totp/ADMIEZO%3Atest%40example.edu?secret=JBSWY3DPEHPK3PXP&issuer=ADMIEZO",
        },
      }),
    });
  });
  await page.route("**/api/v1/auth/mfa/enroll", async (route) => {
    enrollmentPayload = route.request().postDataJSON();
    await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
  });

  await page.goto("/");
  await page.getByLabel("Email address").fill("test@example.edu");
  await page.getByLabel("Password").fill("ChangeMe123!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();

  await expect(page.getByRole("heading", { name: "Set up authenticator" })).toBeVisible();
  await expect(page.locator(".login-totp-setup svg")).toBeVisible();
  await expect(page.getByText("JBSWY3DPEHPK3PXP", { exact: true })).toBeVisible();
  await page.getByLabel("Six-digit code").fill("123456");
  await page.getByRole("button", { name: "Enable and continue" }).click();
  await expect.poll(() => enrollmentPayload).toEqual({
    method_id: "2f64a360-6454-414c-8222-5e95fe4da671",
    code: "123456",
  });
});

test("managed university subdomain selects the tenant before authentication", async ({ page }) => {
  await page.goto("http://northbridge.localhost:3000");
  await expect(page.getByRole("heading", { name: "Northbridge University" })).toBeVisible();
  await expect(page.getByText("Dedicated, isolated university evaluation workspace")).toBeVisible();
  await expect(page.getByLabel("Email address")).toHaveValue("");
  await page.getByLabel("Email address").fill("controller@admiezo.local");
  await page.getByLabel("Password").fill("ChangeMe123!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Evaluation operations" })).toBeVisible();
});

test("platform administrator controls university tenants separately", async ({ page }) => {
  await login(page, "platform@admiezo.local");
  await expect(page.getByRole("heading", { name: "University register" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Exam configuration", exact: true })).toHaveCount(0);
  await expect(page.getByTitle("Open university workspace").first()).toBeVisible();
  await page.getByTitle("Open university workspace").first().click();
  await expect(page.getByRole("heading", { name: "Evaluation operations" })).toBeVisible();
  await navigate(page, "Back to control plane");
  await expect(page.getByRole("heading", { name: "University management" })).toBeVisible();
  await navigate(page, "Platform operations");
  await expect(page.getByRole("heading", { name: "Platform operations" })).toBeVisible();
  await navigate(page, "Platform audit");
  await expect(page.getByRole("heading", { name: "Audit trail" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Cross-university events" })).toBeVisible();
});

test("administrator can open every implemented operational module", async ({ page }) => {
  await login(page);
  const modules = [
    ["Exam configuration", "Exam configuration"],
    ["Evaluator master", "Evaluator master"],
    ["Script receiving", "Script receiving"],
    ["Chain of custody", "Chain of custody"],
    ["Digitization", "Digitization control"],
    ["Anonymization", "Candidate anonymization"],
    ["Script repository", "Script repository"],
    ["Allocation engine", "Allocation engine"],
    ["Assignment control", "Assignment governance"],
    ["Marking schemes", "Marking schemes"],
    ["Evaluation desk", "Evaluation desk"],
    ["Valuation review", "Valuation review"],
    ["Assessment control", "Assessment control"],
    ["Live operations", "Live operations"],
    ["Results & services", "Results & student services"],
    ["Access governance", "Access governance"],
    ["Enterprise settings", "Enterprise settings"],
  ];
  for (const [navigation, heading] of modules) {
    await navigate(page, navigation);
    await expect(page.getByRole("heading", { name: heading, exact: true })).toBeVisible();
    await expect(page.getByText("Unauthorized", { exact: true })).toHaveCount(0);
  }
});

test("administrator can prepare a manual multi-page answer-paper upload", async ({ page }) => {
  await login(page);
  await navigate(page, "Digitization");
  await page.getByRole("button", { name: "Upload answer paper" }).click();

  await expect(page.getByRole("heading", { name: "Upload answer paper" })).toBeVisible();
  await expect(page.getByLabel("Registered script").locator("option")).toHaveCount(2);
  await page.getByLabel("Answer-page images").setInputFiles([
    { name: "page-10.png", mimeType: "image/png", buffer: Buffer.from("page ten") },
    { name: "page-2.png", mimeType: "image/png", buffer: Buffer.from("page two") },
  ]);

  await expect(page.locator(".manual-upload-list li")).toHaveText(["page-2.png", "page-10.png"]);
  await expect(page.getByRole("button", { name: "Upload 2 pages" })).toBeEnabled();
});

test("evaluator opens a real encrypted page through the signed viewer", async ({ page }) => {
  await page.addInitScript(() => {
    const canvas = document.createElement("canvas");
    canvas.width = 640; canvas.height = 480;
    const context = canvas.getContext("2d");
    if (context) { context.fillStyle = "#777"; context.fillRect(0, 0, canvas.width, canvas.height); }
    const stream = canvas.captureStream(10);
    navigator.mediaDevices.getUserMedia = async () => stream;
    navigator.mediaDevices.enumerateDevices = async () => [{ deviceId: "test-camera", groupId: "test", kind: "videoinput", label: "Test camera", toJSON: () => ({}) } as MediaDeviceInfo];
  });
  await login(page, "evaluator1043@admiezo.local");
  const mobile = (page.viewportSize()?.width || 1280) < 900;
  if (mobile) await page.getByTitle("Open navigation").click();
  await expect(page.getByRole("navigation", { name: "Primary navigation" }).getByRole("button")).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Evaluation desk", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Command centre", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Exam configuration", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Allocation engine", exact: true })).toHaveCount(0);
  if (mobile) await page.getByRole("button", { name: "Evaluation desk", exact: true }).click({ force: true });
  const allocation = await page.evaluate(async () => (await fetch("/api/v1/allocation/catalog")).json());
  const available = allocation.assignments.find((item: { locked: boolean; status: string }) => !item.locked && item.status !== "submitted");
  expect(available, "an unlocked evaluator assignment must exist").toBeTruthy();
  const availableRow = page.locator("tbody tr").filter({ hasText: available.script }).first();
  const open = availableRow.getByTitle("Open secure evaluation");
  await expect(open).toBeVisible();
  await open.click();
  await expect(page.getByRole("heading", { name: "Identity verification" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Secure evaluation check" })).toBeVisible();
  await page.getByRole("button", { name: "Run security checks" }).click();
  await expect(page.getByText("Ready", { exact: true })).toBeVisible();
  await page.getByText("I understand that security events may preserve short encrypted webcam clips for authorized human review.").click();
  await page.getByRole("button", { name: "Begin secure evaluation" }).click();
  await expect(page.getByRole("toolbar", { name: "Document viewer controls" })).toBeVisible();
  await expect(page.getByAltText(/Anonymous script page/).first()).toBeVisible();
  await expect(page.getByText("Marks awarded", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Submit valuation" })).toBeVisible();
  await expect(page.getByText(/ADMIEZO · .* · /).first()).toBeVisible();
  if (!mobile) await expect(page.getByText("Connected", { exact: true })).toBeVisible();
  await page.evaluate(() => document.exitFullscreen());
  await expect(page.getByRole("heading", { name: "Evaluation paused" })).toBeVisible();
  await page.getByRole("textbox", { name: "Password" }).fill("ChangeMe123!");
  await page.getByRole("button", { name: "Run checks and resume" }).click();
  await expect(page.getByRole("heading", { name: "Evaluation paused" })).toHaveCount(0);
  if (!mobile && process.env.PLAYWRIGHT_DESTRUCTIVE_SUBMIT === "1") {
    const questionButtons = page.locator(".question-strip button");
    for (let index = 0; index < await questionButtons.count(); index += 1) {
      await questionButtons.nth(index).click();
      await page.getByText("Marks awarded", { exact: true }).locator("..").getByRole("spinbutton").fill("1");
      const saved = page.waitForResponse((response) => response.url().includes("/marking/evaluations/") && response.url().endsWith("/marks") && response.request().method() === "POST");
      await page.getByRole("button", { name: "Save mark" }).click();
      expect((await saved).ok()).toBeTruthy();
      await expect(page.getByText(`${index + 1}/5 questions evaluated`, { exact: true })).toBeVisible();
    }
    const submitted = page.waitForResponse((response) => response.url().includes("/workflow/evaluations/") && response.url().endsWith("/submit") && response.request().method() === "POST");
    await page.getByRole("button", { name: "Submit valuation" }).click();
    expect((await submitted).status()).toBe(200);
    await expect(page.getByText(/Evaluation submitted at .* marks and valuation result locked/)).toBeVisible();
    return;
  }
  const unlocked = page.waitForResponse((response) => response.url().includes("/assignment-governance/assignments/") && response.url().endsWith("/unlock") && response.request().method() === "POST");
  await page.getByTitle("Close viewer").click();
  await unlocked;
  await expect(page.getByRole("toolbar", { name: "Document viewer controls" })).toHaveCount(0);
});

test("remaining modules expose focused operational workspaces", async ({ page }) => {
  await login(page);
  const groups: [string, string[]][] = [
    ["Assessment control", ["Moderation", "Revaluation", "Completion control"]],
    ["Live operations", ["Remote security", "Live monitoring", "Productivity & workload", "Issue management", "Runtime recovery", "Notifications", "Centres & camps", "Low-bandwidth continuity"]],
    ["Results & services", ["Remuneration", "Student services"]],
  ];
  for (const [navigation, tabs] of groups) {
    await navigate(page, navigation);
    for (const tab of tabs) {
      await page.getByRole("tab", { name: new RegExp(tab) }).click();
      await expect(page.getByRole("heading", { name: tab, exact: true })).toBeVisible();
      await expect(page.getByText("Unauthorized", { exact: true })).toHaveCount(0);
    }
  }
});

test("administrator can operate the digitization and valuation workspaces", async ({ page }) => {
  await login(page);
  await navigate(page, "Digitization");
  await expect(page.getByText("Scanner fleet", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Quality control" }).click();
  await expect(page.getByText("Processing runs", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Integrity", exact: true }).click();
  await expect(page.getByText("Signed manifests", { exact: true })).toBeVisible();
  await navigate(page, "Valuation review");
  await expect(page.getByText("Threshold comparison", { exact: true })).toBeVisible();
});
