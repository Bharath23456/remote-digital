# Phase 4 Automation Testing Guide

This guide tests the complete university-to-ADMIEZO flow for:

- Photocopy requests
- Revaluation requests
- Recounting requests
- Admin approval and evaluator allocation
- Final decision, delivery, and audit evidence

The test has three layers:

1. Django backend tests for business rules.
2. Postman/Newman tests for university API integration.
3. Playwright tests for the admin browser workflow.

## 1. Start the local system

Open PowerShell window 1:

```powershell
cd C:\digit\Digital-Evaluation\backend
python manage.py runserver 8000
```

Open PowerShell window 2:

```powershell
cd C:\digit\Digital-Evaluation\frontend
npm run dev
```

Open the application at:

```text
http://localhost:3000
```

Local demo admin login used by the existing browser tests:

```text
Email: admin@admiezo.local
Password: ChangeMe123!
```

Use these credentials only in the local test environment.

## 2. Check backend health

In a third PowerShell window:

```powershell
cd C:\digit\Digital-Evaluation\backend
python manage.py check
python manage.py makemigrations phase4 --check --dry-run
```

Expected:

```text
System check identified no issues
No changes detected in app 'phase4'
```

## 3. Prepare test data

You need one real finalized script UUID. The script must belong to the current university tenant and have the required stored/masked pages.

Use the ADMIEZO admin screens to identify:

- A finalized script UUID
- Two active evaluators
- A locked valuation result for the revaluation test

Use the UUID, not the visible script code. The UUID normally looks like:

```text
8f33e5b7-2c43-4e2b-9b7c-123456789abc
```

Do not change production records. Use a local test database.

## 4. Create the university API key

In the browser:

1. Open **Platform operations**.
2. Open **Integrations**.
3. Open **API keys**.
4. Click **Create API key**.
5. Set source system to `university_portal`.
6. Add scopes `photocopy:request`, `revaluation:request`, and `recounting:request`.
7. Create the key.
8. Copy the complete `admz_live_...` value immediately.

The raw key is displayed only when it is created. The university must store it as a secret.

## 5. Run Postman integration tests

Import these collections in Postman:

```text
C:\digit\Digital-Evaluation\postman\admiezo-photocopy-delivery.postman_collection.json
C:\digit\Digital-Evaluation\postman\admiezo-phase4-revaluation-recounting.postman_collection.json
```

Open the collection variables and set:

```text
baseUrl = http://localhost:8000
apiKey = admz_live_REAL_KEY_HERE
scriptId = REAL_FINALIZED_SCRIPT_UUID
```

### 5.1 Photocopy test

Run these requests in order:

1. **University submits photocopy request**
2. In the browser open **Administration > Photocopy requests**.
3. Refresh and confirm the request appears with status `requested`.
4. Select **Masked release** and click **Approve**.
5. **University downloads approved copy**
6. **University acknowledges delivery**

Expected result:

```json
{
  "status": "delivered"
}
```

The download response must contain:

```json
{
  "evaluator_marks_included": false
}
```

### 5.2 Revaluation test

Run **Submit revaluation**.

Then in the browser:

1. Open **Administration > Revaluation & recounting**.
2. Refresh the register.
3. Approve the request.
4. Assign an evaluator who did not evaluate the original script.
5. Attach the locked valuation result.
6. Decide the final mark.
7. Close the request.

Expected status sequence:

```text
requested -> approved -> assigned -> evaluated -> decided -> closed
```

### 5.3 Recounting test

Run **Submit recounting for missed questions**.

The request must contain:

```json
{
  "scope": "questions",
  "question_ids": ["Q1", "Q2(a)"],
  "request_type": "recounting"
}
```

Complete the same admin sequence as revaluation. Confirm that only the listed questions are reviewed.

## 6. Run Newman from PowerShell

Newman runs the same Postman collections without clicking manually.

From the repository root:

```powershell
npx newman run postman/admiezo-photocopy-delivery.postman_collection.json
npx newman run postman/admiezo-phase4-revaluation-recounting.postman_collection.json
```

For a real university environment, use an environment file instead of putting the API key in the collection:

```powershell
npx newman run postman/admiezo-photocopy-delivery.postman_collection.json -e postman/local-phase4.postman_environment.json
```

Never commit a real API key to Git.

## 7. Run backend automated tests

From the backend directory:

```powershell
cd C:\digit\Digital-Evaluation\backend
python manage.py test apps.revaluation
```

These tests should cover:

- Valid API key and scope
- Invalid API key
- Wrong tenant script
- Photocopy creation
- Masked release
- Unmasked release protection
- Revaluation creation
- Recounting question scope
- Previous evaluator exclusion
- Version conflict protection
- Final mark decision
- Delivery acknowledgement

## 8. Run browser automation

From the frontend directory:

```powershell
cd C:\digit\Digital-Evaluation\frontend
npx playwright test
```

Run only the relevant admin workflow tests:

```powershell
npx playwright test e2e/admin-workflows.spec.ts
npx playwright test e2e/operations.spec.ts
```

Run with the browser visible:

```powershell
npx playwright test --headed
```

Playwright uses the local URL `http://127.0.0.1:3000` by default. Set another URL when needed:

```powershell
$env:PLAYWRIGHT_BASE_URL = "http://127.0.0.1:3000"
npx playwright test
```

## 9. Negative tests

Run these checks deliberately:

| Test | Expected result |
|---|---|
| Replace API key with `YOUR_API_KEY` | Request rejected |
| Remove `photocopy:request` scope | Photocopy request rejected |
| Use a random script UUID | Script not found |
| Recount with `scope: full` | Request rejected |
| Recount without question IDs | Request rejected |
| Download before admin approval | Download blocked |
| Approve unmasked without verified release asset | Approval blocked |
| Assign previous evaluator | Assignment blocked |
| Reuse stale request version | Version conflict returned |

## 10. Evidence that the flow passed

The flow is passed only when all evidence agrees:

1. Postman/Newman returns a successful response.
2. The admin register shows the request.
3. The status changes in the expected order.
4. The correct evaluator is assigned.
5. The final mark or protected copy is produced.
6. The university acknowledgement is stored.
7. The audit trail contains the actions.

Useful outputs after a failed browser test are stored in:

```text
C:\digit\Digital-Evaluation\frontend\test-results
```

## 11. Troubleshooting

`Official portal API key is invalid or lacks scope` means the key is wrong, expired/revoked, or missing the endpoint scope.

`Script not found` means `scriptId` is not a real UUID for the current university tenant.

`403 CSRF` normally applies to admin browser endpoints. Use the ADMIEZO browser for admin actions or provide the admin cookie and CSRF token in Postman.

An empty register normally means the API request was sent to another tenant, the page needs refresh, or the request was rejected before creation.

The simplest complete test is: run the photocopy Postman collection, approve in the browser, then run the download and acknowledgement requests. After that test revaluation and recounting separately.
