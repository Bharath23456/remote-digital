# Phase 4 End-to-End Automation Test Report

**Date:** 2026-09-23  
**Environment:** Local Windows development environment  
**Scope:** Photocopy, revaluation, recounting, admin navigation, delivery, and audit evidence

## Final result

**PASS for the tested local Phase 4 flow.**

The complete local backend flow is green. The official university photocopy API was exercised over HTTP, followed by admin approval, protected copy download, university acknowledgement, and delivery audit evidence. Revaluation and recounting lifecycle rules also pass, including independent evaluator allocation.

## Evidence

### Phase 4 backend suite

Command:

```powershell
cd C:\digit\Digital-Evaluation\backend
python manage.py test apps.phase4
```

Result:

```text
12 tests passed
0 failures
0 errors
```

### Full backend suite

Command:

```powershell
cd C:\digit\Digital-Evaluation\backend
python manage.py test
```

Result:

```text
153 tests passed
0 failures
0 errors
```

### HTTP photocopy lifecycle

The new integration test submits through:

```text
POST /api/v1/phase4/official-portal/photocopy-requests
```

It then verifies:

1. Request is created with status `requested`.
2. Admin login succeeds.
3. Admin approval changes status to `approved`.
4. University download returns `200`.
5. Download response says `evaluator_marks_included: false`.
6. One protected page is returned.
7. University acknowledgement changes status to `delivered`.
8. `student.copy.delivered` audit evidence is stored.

### Revaluation lifecycle

The test verifies:

```text
requested -> approved -> assigned -> evaluated -> decided -> closed
```

It also verifies:

- Locked final mark is required.
- Finalized script is required.
- Complete evaluation/masked pages are required.
- Previous evaluator cannot be assigned again.
- A different active evaluator is accepted.
- New locked valuation result updates the final mark.
- Closing creates an audit event.

### Recounting rules

The test suite verifies that recounting uses selected-question scope and requires question IDs and recounting notes. Recounting cannot be submitted as a full-script request.

### Focused browser test

Command:

```powershell
cd C:\digit\Digital-Evaluation\frontend
npx.cmd playwright test e2e/phase4-navigation.spec.ts --project=chromium
```

Result:

```text
1 test passed
```

Verified in the browser:

- Admin can open **Revaluation & recounting**.
- Admin can open **Photocopy requests**.
- Photocopy screen opens on **Application requests**.

### Frontend lint

Focused Phase 4 files passed with zero errors. One pre-existing warning remains in `operations-app.tsx` for a missing `context.session` hook dependency.

## Bugs and fixes

### Fixed: incomplete Phase 4 test fixture

The tests previously assumed `bootstrap_demo` created a locked final mark, complete evaluation assets, and a previous valuation result. It did not. The test fixture now creates:

- A finalized Phase 4 script.
- Master and evaluation assets.
- Previous evaluator assignment.
- Locked previous evaluation.
- Locked previous valuation result.
- Locked final mark.

### Fixed: photocopy test created only one page asset

Photocopy eligibility requires every script page to have an active evaluation asset. The test now creates an evaluation asset for every page.

### Fixed: audit assertion used the wrong event name

The test expected `student.request.delivered`, while the API correctly records `student.copy.delivered`. The assertion now matches the actual audit contract.

### Fixed: browser test selector ambiguity

The new Phase 4 browser test originally matched both the tab and heading named `Application requests`. It now targets the heading explicitly.

## Broader browser suite note

The existing broad `e2e/operations.spec.ts` suite still has unrelated failures in tenant routing, platform audit navigation, digitization expectations, and evaluator resume state. Those are outside the Phase 4 flow tested here and are not included in the Phase 4 pass result.

## Changed files

- `backend/apps/phase4/tests.py`
- `frontend/e2e/phase4-navigation.spec.ts`
- `docs/phase4-automation-testing-guide.md`
- `output/reports/phase4-automation-test-report.md`

## Conclusion

The local Phase 4 implementation now has passing automated proof for the official photocopy HTTP lifecycle, revaluation rules, recounting rules, admin navigation, and the complete backend test suite. Production certification still requires running the same collections against a staging university API key and staging script data.
