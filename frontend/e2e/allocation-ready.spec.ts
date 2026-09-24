import { expect, test } from "@playwright/test";

test("ready scripts are visible and allocation refresh reloads them", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Email address").fill("admin@admiezo.local");
  await page.getByLabel("Password").fill("ChangeMe123!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Evaluation operations" })).toBeVisible();

  let scriptCode = "AS-READY-001";
  await page.route("**/api/v1/allocation/catalog", async (route) => {
    await route.fulfill({ json: {
      assignments: [],
      scripts: [{ id: "script-1", script_code: scriptCode, paper_id: "paper-1", paper: "CS301-A", state: "stored", version: 1, assigned_rounds: [], next_round: 1 }],
      papers: [{ id: "paper-1", code: "CS301-A", title: "Computer Science", valuation_rounds: 1, second_valuation_mark_threshold: null, stored_scripts: 1, status: "frozen" }], evaluators: [], policies: [], runs: [],
    } });
  });

  await page.getByRole("navigation", { name: "Primary navigation" }).getByRole("button", { name: "Allocation engine" }).click();
  const ready = page.locator("section.panel").filter({ has: page.getByRole("heading", { name: "Ready for allocation" }) });
  await expect(ready.getByRole("row", { name: /AS-READY-001/ })).toBeVisible();
  await expect(page.getByText("No assignments records yet.")).toBeVisible();

  await ready.getByRole("button", { name: "Simulate" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("heading", { name: "Allocation simulation" })).toBeVisible();
  await expect(dialog.getByLabel("Frozen paper")).toHaveValue("paper-1");
  await expect(dialog.getByLabel("Valuation round")).toHaveValue("1");
  await dialog.getByTitle("Close").click();

  scriptCode = "AS-READY-002";
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(ready.getByRole("row", { name: /AS-READY-002/ })).toBeVisible();
  await expect(ready.getByRole("row", { name: /AS-READY-001/ })).toHaveCount(0);
});

test("autonomous AI assignment is separate and redistribution reasons remain editable", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Email address").fill("admin@admiezo.local");
  await page.getByLabel("Password").fill("ChangeMe123!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Evaluation operations" })).toBeVisible();

  await page.route("**/api/v1/allocation/catalog", async (route) => {
    await route.fulfill({ json: {
      assignments: [{ id: "assignment-1", script_id: "assigned-script", script: "AS-ASSIGNED", paper: "CS301-A", evaluator_id: "evaluator-1", evaluator: "Prof. Imran Khan", backup_evaluator_id: null, backup_evaluator: null, valuation_round: 1, status: "assigned", quality_score: 93, source: "simulation", priority: 3, is_flagged: false, due_at: "2026-09-24T16:12:57+05:30", version: 1 }],
      scripts: [{ id: "script-1", script_code: "AS-AI-READY", paper_id: "paper-1", paper: "CS301-A", state: "stored", version: 1, assigned_rounds: [], next_round: 1 }],
      papers: [{ id: "paper-1", code: "CS301-A", title: "Database Management Systems", valuation_rounds: 1, second_valuation_mark_threshold: null, stored_scripts: 1, status: "frozen" }],
      evaluators: [], policies: [], runs: [],
    } });
  });
  await page.route("**/api/v1/ai-evaluation/catalog", async (route) => {
    await route.fulfill({ json: {
      provider: { provider: "admiezo_ai", configured: true, valid: true, available: true, message: "ADMIEZO AI Assistant connection verified" },
      governance: { mode: "autonomous", confidence_threshold: 85, model_name: "admiezo-ai-v1" },
      papers: [
        { id: "paper-1", code: "CS301-A", title: "Database Management Systems", subject: "CS301", status: "frozen", ready_scripts: 1, reference_pack: { readiness: { ready: true, question_paper: true, reference_answers: 3, configured_questions: 5, total_questions: 5, missing_question_guides: 0 } } },
        { id: "paper-2", code: "CS401-A", title: "Distributed Systems", subject: "CS401", status: "frozen", ready_scripts: 0, reference_pack: { readiness: { ready: false, question_paper: true, reference_answers: 3, configured_questions: 0, total_questions: 5, missing_question_guides: 5 } } },
      ],
      analyses: [],
    } });
  });
  let assignmentRequest: Record<string, unknown> | null = null;
  await page.route("**/api/v1/ai-evaluation/assignments/all-ready", async (route) => {
    assignmentRequest = route.request().postDataJSON();
    await route.fulfill({ json: { queued: 1, analysis_ids: ["analysis-1"] } });
  });

  await page.getByRole("navigation", { name: "Primary navigation" }).getByRole("button", { name: "Allocation engine" }).click();
  await expect(page.getByRole("button", { name: "Assign all ready scripts to AI" })).toBeVisible();

  await page.getByRole("button", { name: "Redistribute" }).click();
  const redistribution = page.getByRole("dialog");
  await redistribution.getByLabel("Redistribution reason").fill("Evaluator is unavailable for this deadline");
  await expect(redistribution.getByLabel("Redistribution reason")).toHaveValue("Evaluator is unavailable for this deadline");
  await redistribution.getByTitle("Close").click();

  await page.getByRole("button", { name: "Assign all ready scripts to AI" }).click();
  const confirmation = page.getByRole("dialog");
  await expect(confirmation.getByText("1 ready script")).toBeVisible();
  await confirmation.getByRole("button", { name: "Assign all to AI" }).click();
  await expect.poll(() => assignmentRequest).toEqual({ maximum_scripts: 5000 });
  await expect(page.getByText("1 script queued for autonomous evaluation")).toBeVisible();

  await page.getByRole("navigation", { name: "Primary navigation" }).getByRole("button", { name: "ADMIEZO AI Assistant" }).click();
  await page.getByLabel("Paper and subject").selectOption("paper-2");
  await expect(page.getByText("AI setup is not ready")).toBeVisible();
  await expect(page.getByText("Complete question text for 5 questions")).toBeVisible();
  await expect(page.getByText("No unassigned stored scripts are ready for this paper")).toBeVisible();
});
