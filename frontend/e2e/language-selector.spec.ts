import { expect, test } from "@playwright/test";

const context = {
  user: { id: 1, name: "Admin User", email: "admin@example.edu" },
  tenant: { id: "northbridge", name: "Northbridge University", code: "NBU" },
  role: "university_admin",
  permissions: [],
  must_change_password: false,
  enabled_modules: ["configuration", "evaluators", "receiving", "custody", "digitization", "anonymisation", "repository", "allocation", "assignment_governance", "rubrics", "evaluation", "valuation", "assessment", "operations", "security", "audit", "enterprise"],
  ai_evaluation: { mode: "disabled", confidence_threshold: 85, model_name: "admiezo-ai-v1", provider: { available: false }, available: false },
  tenants: [{ id: "northbridge", name: "Northbridge University", role: "university_admin" }],
  session: { id: "language-test", timeout_minutes: 60 },
};

const overview = {
  generated_at: new Date().toISOString(),
  session: { name: "November 2026 End Semester", term: "November 2026", status: "active" },
  metrics: { expected_scripts: 1048, received_scripts: 1037, assigned_scripts: 36, registered_scripts: 50, evaluation_progress: 33 },
  pipeline: [], attention: [], module_counts: {}, papers: [], recent_events: [],
};

const evaluator = {
  id: "evaluator-1", evaluator_code: "EV-1042", display_name: "Dr. Kavya Rao", email: "evaluator1042@admiezo.local", mobile: "+919900001042", employee_id: "NBU-FAC-1042",
  institution_name: "Northbridge University", department: "Computer Science", designation: "Associate Professor", qualification: "PhD", employment_type: "permanent", years_experience: 8,
  grade: "evaluator", status: "active", daily_capacity: 18, available_from: null, available_to: null, version: 1, expertise: [], availability: [], custom_fields: {}, login_enabled: true,
};

const assignment = {
  id: "assignment-1", script: "AS-000013", paper: "CS402-A", page_count: 3, valuation_round: 1, status: "in_progress", priority: 3,
  is_flagged: false, locked: false, draft_saved_at: null, last_opened_at: null, last_page: 1, progress_percent: 0, due_at: "2026-09-19T12:58:49Z", version: 1,
};

test("language menu translates the dashboard and persists the choice", async ({ page }) => {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = path.endsWith("/auth/me") ? context
      : path.endsWith("/operations/overview") ? overview
        : path.endsWith("/evaluator-management") ? [evaluator]
          : path.endsWith("/allocation/catalog") ? { assignments: [assignment] }
          : path.endsWith("/eligibility") ? { verifications: [], eligibility: [], history: [] }
            : path.endsWith("/configuration/catalog") ? { subjects: [] }
              : path.endsWith("/enterprise/form-fields") ? { fields: [] }
        : path.endsWith("/auth/notifications") ? []
          : [];
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });

  await page.goto("/");
  await expect(page.getByRole("button", { name: /Select language/ })).toContainText("Language");
  await page.getByRole("button", { name: /Select language/ }).click();
  await expect(page.getByRole("menuitemradio")).toHaveCount(6);
  await page.getByRole("menuitemradio", { name: /ಕನ್ನಡ/ }).click();

  await expect(page.locator("html")).toHaveAttribute("lang", "kn");
  await expect(page.getByText("ಕಾರ್ಯಾಚರಣೆಗಳು", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "ಕಮಾಂಡ್ ಕೇಂದ್ರ", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "ಪರೀಕ್ಷಾ ಸಂರಚನೆ", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "ಮೌಲ್ಯಮಾಪನ ಕಾರ್ಯಾಚರಣೆಗಳು", exact: true })).toBeVisible();
  await expect(page.getByText("೧,೦೪೮", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "ಮೌಲ್ಯಮಾಪಕರ ಮಾಸ್ಟರ್", exact: true }).click();
  await expect(page.getByText("ಡಾ. ಕಾವ್ಯ ರಾವ್", { exact: true })).toBeVisible();
  await expect(page.getByRole("banner").getByText("ನಾರ್ತ್‌ಬ್ರಿಡ್ಜ್ ವಿಶ್ವವಿದ್ಯಾಲಯ", { exact: true })).toBeVisible();
  await expect(page.getByText("ಕಂಪ್ಯೂಟರ್ ವಿಜ್ಞಾನ", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "ಮೌಲ್ಯಮಾಪನ ಡೆಸ್ಕ್", exact: true }).click();
  await expect(page.getByText("ಎಎಸ್-೦೦೦೦೧೩", { exact: true })).toBeVisible();
  await expect(page.getByText("ಸಿಎಸ್೪೦೨-ಎ", { exact: true })).toBeVisible();
  await expect(page.getByText("೦%", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("ಪ್ರಗತಿಯಲ್ಲಿದೆ", { exact: true }).last()).toBeVisible();
  await expect(page.getByRole("row", { name: /ಎಎಸ್-೦೦೦೦೧೩/ })).not.toContainText(/[A-Z0-9]/);

  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("lang", "kn");
  await expect(page.getByRole("button", { name: /ಭಾಷೆಯನ್ನು ಆಯ್ಕೆಮಾಡಿ/ })).toContainText("KN");
});
