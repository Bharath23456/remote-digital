import { expect, test, type Page } from "@playwright/test";

type DeskRole = "university_admin" | "bundle_preparer" | "intake_receiver" | "scan_operator";

test.beforeEach(async ({}, testInfo) => {
  test.skip(testInfo.project.name !== "chromium", "Intake workstations are desktop-only");
});

async function mockDesk(page: Page, role: DeskRole) {
  const modules = role === "university_admin" ? ["receiving", "custody", "digitization", "anonymisation"] : {
    bundle_preparer: ["receiving"], intake_receiver: ["custody"], scan_operator: ["digitization"],
  }[role];
  await page.route("**/api/v1/enterprise/domain-context", (route) => route.fulfill({ json: {
    scope: "university", hostname: "northbridge.localhost", university: {
      id: "tenant-1", name: "Northbridge University", code: "NBU", status: "active",
      branding: { name: "Northbridge University", description: "", theme: "forest", logo_url: "" },
    },
  } }));
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({ json: {
    user: { id: 1, name: "Intake worker", email: "worker@example.test" },
    tenant: { id: "tenant-1", name: "Northbridge University", code: "NBU" },
    role, permissions: [], must_change_password: false, enabled_modules: modules,
    branding: { name: "Northbridge University", description: "", theme: "forest", logo_url: "" },
    platform_host: false, platform_url: "http://localhost:3000",
    ai_evaluation: { mode: "disabled", confidence_threshold: 85, model_name: "admiezo-ai-v1", provider: { available: false }, available: false },
    tenants: [{ id: "tenant-1", name: "Northbridge University", role }],
    session: { id: "session-1", timeout_minutes: 30 },
  } }));
  await page.route("**/api/v1/operations/overview", (route) => route.fulfill({ json: {
    generated_at: new Date().toISOString(), session: null, metrics: {}, pipeline: [], attention: [], module_counts: {}, papers: [], recent_events: [],
  } }));
  await page.route("**/api/v1/auth/notifications", (route) => route.fulfill({ json: { items: [], unread_count: 0 } }));
  await page.route("**/api/v1/auth/csrf", (route) => route.fulfill({ json: { csrf_token: "test-csrf" } }));
  await page.route("**/api/v1/receiving/guided/papers", (route) => route.fulfill({ json: { papers: [{ id: "paper-1", code: "CS402-A", title: "Cryptography" }] } }));
  await page.route("**/api/v1/receiving/guided/preparation", (route) => route.fulfill({ json: {
    papers: [{ id: "paper-1", code: "CS402-A", title: "Cryptography" }],
    colleges: [{ id: "college-1", code: "central-college", name: "Central College" }],
    packets: [],
  } }));
  await page.route("**/api/v1/receiving/guided/catalog", (route) => route.fulfill({ json: { enabled: true, bundles: [{
    id: "bundle-1", barcode: "BND-2026-001", source_centre: "Central College", mode: "transfer", status: "received",
    expected_packets: 1, received_packets: 1, expected_scripts: 2, scanned_scripts: 1,
    packets: [{ id: "packet-1", barcode: "PKT-2026-001", subject: "CS402-A", status: "scanning", expected_scripts: 2, scanned_scripts: 1, missing_count: 1, missing_references: ["A1B2C3D4E5F6"] }],
  }] } }));
  await page.route("**/api/v1/receiving/guided/lookup/bundles/BND-2026-001", (route) => route.fulfill({ json: {
    id: "bundle-1", barcode: "BND-2026-001", source_centre: "Central College", mode: "transfer", status: "received",
    expected_packets: 1, received_packets: 1, expected_scripts: 2, scanned_scripts: 1,
    packets: [{ id: "packet-1", barcode: "PKT-2026-001", subject: "CS402-A", status: "scanning", expected_scripts: 2, scanned_scripts: 1, missing_count: 1, missing_references: ["A1B2C3D4E5F6"] }],
  } }));
  await page.route("**/api/v1/receiving/guided/lookup/packets/PKT-2026-001", (route) => route.fulfill({ json: {
    id: "packet-1", barcode: "PKT-2026-001", subject: "CS402-A", status: "scanning", expected_scripts: 2, scanned_scripts: 1, missing_count: 1, missing_references: ["A1B2C3D4E5F6"], bundle: "BND-2026-001",
  } }));
  await page.goto("/");
}

async function navigate(page: Page, label: string) {
  if ((page.viewportSize()?.width || 0) <= 760) await page.getByTitle("Open navigation").click();
  await page.getByRole("navigation", { name: "Primary navigation" }).getByRole("button", { name: label, exact: true }).click();
}

test("admin sees separate preparation, receipt, and digitization tasks", async ({ page }) => {
  await mockDesk(page, "university_admin");
  await navigate(page, "Bundle preparation");
  await expect(page.locator(".page-heading .eyebrow")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Scan scripts into a packet" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Ready packets" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Recent bundles" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Receive bundle" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Refresh", exact: true })).toHaveCount(1);
  await navigate(page, "Chain of custody");
  await expect(page.getByRole("heading", { name: "Receive bundle" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Scan scripts into a packet" })).toHaveCount(0);
  await page.getByRole("button", { name: "Open packets" }).click();
  await expect(page.getByRole("heading", { name: "BND-2026-001" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "PKT-2026-001" })).toBeVisible();
  await navigate(page, "Digitization");
  await expect(page.locator(".page-heading .eyebrow")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Open packet" })).toBeVisible();
  await page.getByPlaceholder("Scan or type packet barcode").fill("PKT-2026-001");
  await page.getByRole("button", { name: "Open packet" }).click();
  await expect(page.getByRole("heading", { name: "PKT-2026-001" })).toBeVisible();
  await expect(page.getByText("1/2 scripts scanned")).toBeVisible();
  await expect(page.getByRole("button", { name: "Upload script" })).toBeVisible();
});

test("bundle, packet and script move through the three desks", async ({ page }) => {
  await mockDesk(page, "university_admin");
  let bundleStatus = "";
  let packetStatus = "registered";
  let scanned = 0;
  let preparedPackets: Array<Record<string, unknown>> = [];
  await page.route("**/api/v1/receiving/guided/preparation", (route) => route.fulfill({ json: {
    papers: [{ id: "paper-1", code: "CS402-A", title: "Cryptography" }],
    colleges: [{ id: "college-1", code: "test-college", name: "Test College" }],
    packets: preparedPackets,
  } }));
  await page.route("**/api/v1/receiving/guided/catalog", (route) => route.fulfill({ json: { enabled: true, bundles: bundleStatus ? [{
    id: "bundle-2", barcode: "BND-TEST-002", source_centre: "Test College", mode: "transfer", status: bundleStatus,
    expected_packets: 1, received_packets: packetStatus === "registered" ? 0 : 1, expected_scripts: 1, scanned_scripts: scanned,
    packets: [{ id: "packet-2", barcode: "PKT-TEST-002", subject: "CS402-A", status: packetStatus, expected_scripts: 1, scanned_scripts: scanned, missing_count: 1 - scanned, missing_references: scanned ? [] : ["HASHED-QR"] }],
  }] : [] } }));
  await page.route("**/api/v1/receiving/guided/lookup/bundles/BND-TEST-002", (route) => route.fulfill({ json: {
    id: "bundle-2", barcode: "BND-TEST-002", source_centre: "Test College", mode: "transfer", status: bundleStatus,
    expected_packets: 1, received_packets: packetStatus === "registered" ? 0 : 1, expected_scripts: 1, scanned_scripts: scanned,
    packets: [{ id: "packet-2", barcode: "PKT-TEST-002", subject: "CS402-A", status: packetStatus, expected_scripts: 1, scanned_scripts: scanned, missing_count: 1 - scanned, missing_references: scanned ? [] : ["HASHED-QR"] }],
  } }));
  await page.route("**/api/v1/receiving/guided/lookup/packets/PKT-TEST-002", (route) => route.fulfill({ json: {
    id: "packet-2", barcode: "PKT-TEST-002", subject: "CS402-A", status: packetStatus, expected_scripts: 1, scanned_scripts: scanned, missing_count: 1 - scanned, missing_references: scanned ? [] : ["HASHED-QR"], bundle: "BND-TEST-002",
  } }));
  await page.route("**/api/v1/receiving/guided/prepared-packets", async (route) => {
    const packet = {
      id: "prepared-2", barcode: "PKT-TEST-002", paper_id: "paper-1", subject: "CS402-A", paper_title: "Cryptography",
      source_college_id: "college-1", source_college: "Test College", expected_scripts: 1, status: "ready", version: 1,
      created_at: new Date().toISOString(),
    };
    preparedPackets = [packet];
    await route.fulfill({ json: packet });
  });
  await page.route("**/api/v1/receiving/guided/bundles/from-packets", async (route) => {
    bundleStatus = "registered";
    preparedPackets = preparedPackets.map((packet) => ({ ...packet, status: "bundled" }));
    await route.fulfill({ json: { id: "bundle-2", barcode: "BND-TEST-002", status: bundleStatus } });
  });
  await page.route("**/api/v1/receiving/guided/bundles/start", async (route) => {
    bundleStatus = "in_transit";
    await route.fulfill({ json: { status: bundleStatus } });
  });
  await page.route("**/api/v1/receiving/guided/bundles/receive", async (route) => {
    bundleStatus = "received";
    await route.fulfill({ json: { status: bundleStatus, expected_packets: 1 } });
  });
  await page.route("**/api/v1/receiving/guided/packets/receive", async (route) => {
    packetStatus = "received";
    await route.fulfill({ json: { status: packetStatus, subject: "CS402-A", expected_scripts: 1 } });
  });
  await page.route("**/api/v1/receiving/guided/packets/packet-2/recognize", (route) => route.fulfill({ json: { script_id: "script-2", script_code: "AS-OPAQUE-002", version: 1, subject: "CS402-A", identity_linked: true } }));
  await page.route("**/api/v1/repository/manual-scan/uploads", (route) => route.fulfill({ json: { id: "upload-1", version: 1, upload_url: "/api/v1/test-upload", headers: {} } }));
  await page.route("**/api/v1/test-upload", (route) => route.fulfill({ status: 200, body: "" }));
  await page.route("**/api/v1/repository/uploads/upload-1/finalize", (route) => route.fulfill({ json: { status: "completed" } }));
  await page.route("**/api/v1/repository/scripts/script-2/complete-scan", async (route) => {
    packetStatus = "complete"; scanned = 1;
    await route.fulfill({ json: { status: "scanned" } });
  });
  await page.route("**/api/v1/anonymisation/scripts/script-2/auto-mask", (route) => route.fulfill({ json: { status: "masked" } }));

  await navigate(page, "Bundle preparation");
  await page.getByRole("textbox", { name: "Packet barcode" }).fill("PKT-TEST-002");
  await page.getByRole("combobox", { name: "Subject / paper" }).selectOption("paper-1");
  await page.getByRole("textbox", { name: "Booklet QR code" }).fill("QR-TEST-002");
  await page.getByRole("button", { name: "Add booklet" }).click();
  await page.getByRole("button", { name: "Save ready packet" }).click();
  await expect(page.getByText("PKT-TEST-002 is ready with 1 expected scripts.")).toBeVisible();
  await page.getByRole("button", { name: "Create bundle", exact: true }).click();
  await page.getByRole("textbox", { name: "Bundle barcode" }).fill("BND-TEST-002");
  await page.getByRole("checkbox", { name: "Select PKT-TEST-002" }).check();
  await page.getByRole("button", { name: "Create and dispatch" }).click();
  await expect(page.getByText("Bundle BND-TEST-002 dispatched to the university.")).toBeVisible();
  await navigate(page, "Chain of custody");
  await page.getByRole("textbox", { name: "Bundle barcode" }).fill("BND-TEST-002");
  await page.getByRole("button", { name: "Receive bundle" }).click();
  await expect(page.getByRole("heading", { name: "BND-TEST-002" })).toBeVisible();
  await page.getByRole("textbox", { name: "Packet barcode" }).fill("PKT-TEST-002");
  await page.getByRole("button", { name: "Receive packet" }).click();
  await expect(page.getByText("PKT-TEST-002 received - CS402-A, 1 scripts expected.")).toBeVisible();
  await navigate(page, "Digitization");
  await page.getByPlaceholder("Scan or type packet barcode").fill("PKT-TEST-002");
  await page.getByRole("button", { name: "Open packet" }).click();
  await page.getByLabel("Front page").setInputFiles({ name: "page-1.png", mimeType: "image/png", buffer: Buffer.from("test-image") });
  await page.getByRole("button", { name: "Upload script" }).click();
  await expect(page.getByText("AS-OPAQUE-002 - 1 pages stored and identity cover masked.")).toBeVisible();
  await expect(page.getByText("1/1 scripts scanned")).toBeVisible();
});

test("unreadable cover offers demo-only manual QR and USN entry", async ({ page }) => {
  await mockDesk(page, "scan_operator");
  await page.route("**/api/v1/receiving/guided/lookup/packets/PKT-2026-001", (route) => route.fulfill({ json: {
    id: "packet-1", barcode: "PKT-2026-001", subject: "CS402-A", status: "scanning", expected_scripts: 2,
    scanned_scripts: 1, missing_count: 1, missing_references: ["A1B2C3D4E5F6"], bundle: "BND-2026-001", manual_recognition_enabled: true,
  } }));
  let attempts = 0;
  await page.route("**/api/v1/receiving/guided/packets/packet-1/recognize", async (route) => {
    attempts += 1;
    if (attempts === 1) {
      await route.fulfill({ status: 422, json: { detail: "USN has an unfilled or ambiguous bubble" } });
      return;
    }
    const body = route.request().postData() || "";
    expect(body).toContain("QR-TEST-002");
    expect(body).toContain("4UB22CS032");
    await route.fulfill({ json: { script_id: "script-2", script_code: "AS-OPAQUE-002", version: 1, subject: "CS402-A", identity_linked: true } });
  });
  await page.route("**/api/v1/repository/manual-scan/uploads", (route) => route.fulfill({ json: { id: "upload-1", version: 1, upload_url: "/api/v1/test-upload", headers: {} } }));
  await page.route("**/api/v1/test-upload", (route) => route.fulfill({ status: 200, body: "" }));
  await page.route("**/api/v1/repository/uploads/upload-1/finalize", (route) => route.fulfill({ json: { status: "completed" } }));
  await page.route("**/api/v1/repository/scripts/script-2/complete-scan", (route) => route.fulfill({ json: { status: "scanned" } }));
  await page.route("**/api/v1/anonymisation/scripts/script-2/auto-mask", (route) => route.fulfill({ json: { status: "masked" } }));

  await page.getByPlaceholder("Scan or type packet barcode").fill("PKT-2026-001");
  await page.getByRole("button", { name: "Open packet" }).click();
  await page.getByLabel("Front page").setInputFiles({ name: "page-1.png", mimeType: "image/png", buffer: Buffer.from("test-image") });
  await page.getByRole("button", { name: "Upload script" }).click();
  await expect(page.getByLabel("Booklet QR code")).toBeVisible();
  await page.getByLabel("Booklet QR code").fill("QR-TEST-002");
  await page.getByLabel("USN from front page").fill("4UB22CS032");
  await page.getByRole("button", { name: "Confirm details and upload" }).click();
  await expect(page.getByText("AS-OPAQUE-002 - 1 pages stored and identity cover masked.")).toBeVisible();
  await expect(page.getByLabel("USN from front page")).toHaveCount(0);
  expect(attempts).toBe(2);
});

for (const desk of [
  { role: "bundle_preparer", page: "Bundle preparation", heading: "Scan scripts into a packet" },
  { role: "intake_receiver", page: "Chain of custody", heading: "Receive bundle" },
  { role: "scan_operator", page: "Digitization", heading: "Open packet" },
] as const) {
  test(`${desk.role} sees only its assigned desk`, async ({ page }) => {
    let overviewCalled = false;
    await mockDesk(page, desk.role);
    await page.route("**/api/v1/operations/overview", (route) => { overviewCalled = true; return route.fulfill({ status: 403, json: { detail: "Forbidden" } }); });
    await expect(page.getByRole("heading", { name: desk.heading })).toBeVisible();
    if ((page.viewportSize()?.width || 0) <= 760) await page.getByTitle("Open navigation").click();
    const navigation = page.getByRole("navigation", { name: "Primary navigation" });
    await expect(navigation.getByRole("button")).toHaveCount(1);
    await expect(navigation.getByRole("button", { name: desk.page })).toBeVisible();
    if ((page.viewportSize()?.width || 0) <= 760) await page.getByTitle("Close navigation").click();
    await expect(page.getByRole("button", { name: "Refresh", exact: true })).toHaveCount(1);
    await page.getByRole("button", { name: "Refresh", exact: true }).click();
    await expect(page.getByRole("heading", { name: desk.heading })).toBeVisible();
    expect(overviewCalled).toBe(false);
  });
}
