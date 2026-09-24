# Phase 4 Moderation Test Report

## Scope

This report covers the Phase 4 moderation workflow from policy setup through final-mark update and completion re-signing.

## Tested Flow

```text
Moderation policy setup
  -> Script sampling
  -> Independent moderator assignment
  -> Moderation review
  -> Adjusted mark and reason recorded
  -> Independent approval
  -> Locked final mark updated
  -> Checksum recalculated
  -> Completion/sign-off reopened
  -> Audit event recorded
```

## Detailed Operational Flow

| Step | Status | Responsible actor | Action | Output |
|---|---|---|---|---|
| 1 | Policy configured | Admin | Select paper, sampling percentage, sampling rules and mandatory flag. | Versioned moderation policy. |
| 2 | Sampled | Admin/system | Run sampling against locked valuation results. | Moderation cases with sampling reasons. |
| 3 | Assigned | Admin | Select an active independent moderator. | Case linked to moderator. |
| 4 | Review | Moderator | Inspect the script and valuation evidence. | Case ready for decision. |
| 5 | Decided | Moderator | Enter adjusted mark, evidence snapshot and reason. | Versioned moderation decision. |
| 6 | Approved | Independent approver | Review and approve the decision. The decision maker cannot approve their own case. | Approved moderation case. |
| 7 | Final mark updated | System | Apply the approved mark to the locked final mark and recalculate the checksum. | Official final result reflects moderation. |
| 8 | Completion reopened | System | Invalidate previous completion declaration and signature. | Fresh completion review is required. |
| 9 | Audited | System | Record every transition and mark change. | Immutable audit trail. |

## End Conditions

The moderation flow ends only when:

1. The case status is `approved`.
2. The official `FinalMark` contains the approved adjusted mark.
3. The final-mark checksum has been recalculated.
4. Any previous completion signature has been invalidated.
5. A new completion review and sign-off can be performed.
6. Audit events exist for sampling, assignment, decision, approval and final-mark update.

```text
Approved moderation case
        |
        v
Official final mark updated
        |
        v
Checksum recalculated
        |
        v
Previous completion signature invalidated
        |
        v
Completion review and fresh sign-off
        |
        v
Final result ready for controlled release
```

## Results

| Area | Result |
|---|---|
| Django system checks | Passed |
| Phase 4 test suite | Passed: 14 tests |
| Moderation policy validation | Passed |
| Script sampling | Passed |
| Moderator assignment | Passed |
| Original evaluator conflict prevention | Passed |
| Active moderator validation | Passed |
| Adjusted mark validation | Passed |
| Independent approval | Passed |
| Official final-mark update | Passed |
| Final-mark checksum update | Passed |
| Completion re-sign requirement | Passed |
| Audit event creation | Passed |
| Stale-version protection | Passed |

## Confirmed Business Rules

- A moderation policy is unique per paper.
- Sampling percentage must be between 0 and 100.
- Only locked valuation results can enter moderation.
- The original evaluator cannot moderate the same script.
- The assigned moderator must be active.
- Adjusted marks must be within the paper maximum.
- A decision requires a specific reason.
- The moderator cannot approve their own decision.
- Approval updates the official locked final mark.
- The final-mark checksum is recalculated after approval.
- Existing completion signatures are invalidated after a moderation change.
- Every important transition creates an audit event.

## Implementation Checked

- `backend/apps/phase4/services.py`
- `backend/apps/phase4/api.py`
- `backend/apps/phase4/tests.py`
- `frontend/src/components/advanced-operations-workspace.tsx`
- `frontend/src/components/operations-app.tsx`

## Test Command

```powershell
python backend/manage.py check
python backend/manage.py test apps.phase4 --verbosity 1
```

## Final Status

**PASSED for the implemented Phase 4 moderation workflow.**

Before production release, run one staging test using real masked scripts, approved moderator accounts, production-like object storage, and the university result-release process.

## Screen Evidence

The related Phase 4 operational screens used during verification are included in the repository:

- [Revaluation and recounting screen](../screenshots/phase4-revaluation-recounting.png)
- [Photocopy requests screen](../screenshots/phase4-photocopy-requests.png)

Moderation is available from `Evaluation -> Assessment control -> Moderation` in the current UI. The assessment-control workspace separates moderation from revaluation and completion control.
