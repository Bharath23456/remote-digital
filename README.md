# ADMIEZO

ADMIEZO is a multi-university answer-script evaluation operations platform. It runs a Next.js 16 frontend, a Django 5/Django Ninja evaluation core, an isolated Django identity service, an encrypted storage gateway, PostgreSQL, and a transactional outbox worker.

## Operational RBAC

Platform and university administrators can create users and assign operational roles from **Access governance → User access**. Operational roles have fixed least-privilege module access, enforced by both the UI and API:

| Role | Module | Main responsibility |
| --- | --- | --- |
| Script receiver | Receiving | Receive dispatches, packets and bundles; reconcile receiving exceptions |
| Scanner operator | Digitization | Run scanner batches, scan processing, manual uploads and integrity checks |
| Chain custody officer | Chain of custody | Register scripts, scan barcodes, reconcile packets and control transfers |

Role and module assignment is administrator-only. Do not put administrator or temporary passwords in source files. After pulling changes, run `docker compose up --build`; Django applies the role migration during backend startup. Sign in at `http://localhost:3000`, open **Access governance**, and create the operational users there.

## Implemented modules

- 01 Digital Evaluation Administration and Configuration
- 02 Evaluator Master Management
- 03 Evaluator Verification and Eligibility
- 04 Evaluator Authentication and Session Access
- 06 Evaluator Assignment and Allocation Engine
- 07 Assignment Governance and Security
- 08 Physical Answer Script Receiving
- 09 Script Identification, Barcode and Chain of Custody
- 10 High-Speed Scanning
- 11 Scan Processing and Script Quality Control
- 12 Script Anonymization and Identity Separation
- 13 Digital Script Repository
- 14 Digital Evaluation Viewer
- 15 Digital Annotation
- 16 Question-Wise Evaluation
- 17 Marking Scheme and Rubrics
- 18 Evaluation Workflow and Guided Operations
- 23 Multi-Valuation Engine
- 24 Moderation
- 25 Discrepancy and Reconciliation Management
- 27 Revaluation
- 28 Evaluation Completion and Final Mark Control
- 29 Remote Evaluation Security
- 31 Live Evaluation Monitoring
- 32 Productivity and Workload Management
- 33 Issue Management
- 34 Runtime Recovery
- 35 Notification Management
- 36 Evaluation Centres and Camps
- 37 Offline and Low-Bandwidth Continuity
- 38 Evaluator Remuneration
- 39 Student Script Services
- 40 Data Security and Access Governance
- 41 Script Integrity and Anti-Tampering
- 42 Audit and Forensics
- 46 Integration Management
- 47 Multi-University and Enterprise Configuration
- 48 Interface Internationalization
- 49 Disaster Recovery

These are working workflows, not static screens. Writes are tenant-scoped and authorized, workflow transitions are validated, audit and outbox records share the domain transaction, submit/finalize calls are idempotent, candidate PII stays in the isolated encrypted identity service, and script bytes travel directly between the browser and five-minute signed storage URLs.

## AI evaluation

The university-wide **ADMIEZO AI Assistant** policy supports **No AI**, evaluator-only **AI assisted evaluation**, and background **Autonomous AI evaluation** modes. A Super Admin configures each university's mode, confidence threshold, and encrypted provider credential while provisioning the university or from its settings. University Access Governance displays this platform-managed policy without allowing tenant administrators to replace it. Autonomous evaluation requires one question paper, three reference answers, question guidance, and a frozen marking scheme. Only masked evaluation assets are processed; a result at or above the configured confidence threshold enters the existing valuation/final-mark workflow, while a lower-confidence result is assigned to an eligible human without creating AI marks.

## What each module does

### Module 01: Digital Evaluation Administration and Configuration

This module defines the academic and examination structure used by every downstream workflow.

- Manages academic years, programmes, subjects, examination sessions, papers, questions, marks, pass marks, valuation rounds, and evaluation rules.
- Moves papers through controlled draft, review, approval, freeze, and go-live states.
- Uses versioned change requests and independent approvals so live examination configuration cannot be silently modified.
- Provides readiness checks that identify missing or invalid setup before an examination is activated.

### Module 02: Evaluator Master Management

This is the authoritative directory of evaluators who can participate in evaluation work.

- Stores evaluator identity references, employee IDs, contact details, qualifications, experience, expertise, and availability periods.
- Supports profile editing and controlled lifecycle transitions including onboarding, active, inactive, suspended, debarred, and retired states.
- Maintains immutable lifecycle history and shows real allocation and evaluation work history.
- Supplies evaluator capacity, expertise, and availability information to eligibility and allocation workflows.

### Module 03: Evaluator Verification and Eligibility

This module decides whether an evaluator is permitted to evaluate a particular subject.

- Creates verification cases and collects qualification or employment evidence through direct, signed storage uploads.
- Records duplicate, fraud, conflict-of-interest, debarment, blacklist, qualification, experience, institution, and expertise checks.
- Requires independent multi-level review before verification is approved.
- Produces subject-specific eligibility records with expiry dates, revalidation, suspension, and a complete decision history.
- Prevents the allocation engine from assigning an evaluator with an expired, blocked, or ineligible record.

### Module 04: Evaluator Authentication and Session Access

This module controls how users authenticate and how their active sessions are secured.

- Provides password login, email OTP, TOTP authenticator setup, passkeys, trusted devices, and recovery workflows.
- Supports institutional OIDC single sign-on with discovery, PKCE, state, nonce, signature, issuer, audience, and claim validation.
- Tracks active sessions, expiry, device and location context, risk scores, step-up authentication, and administrative revocation.
- Keeps sensitive identity credentials in the isolated identity service instead of the evaluation database.
- Records successful, failed, challenged, and revoked authentication events for security review.

### Module 06: Evaluator Assignment and Allocation Engine

This module assigns anonymized scripts to eligible evaluators without exposing candidate identity.

- Configures allocation policies for workload, deadlines, valuation rounds, expertise, balancing, backup evaluators, and conflict controls.
- Simulates an allocation plan before execution and provides quality scores, blockers, and workload projections.
- Executes manual or intelligent allocation only when eligibility, expertise, availability, capacity, and conflict checks pass.
- Supports acceptance, rejection, start, progress, submission, and controlled redistribution to a backup or replacement evaluator.
- Uses optimistic locking and immutable assignment history to prevent silent concurrent updates.

### Module 07: Assignment Governance and Security

This module controls who can work on an assignment and prevents simultaneous or unauthorized marking.

- Defines assignment access, deadline, lock duration, idle timeout, reassignment, and approval policies by institution.
- Issues short-lived exclusive evaluator locks as hashed tokens and rejects stale, expired, or competing sessions.
- Routes sensitive reassignment through request and independent approval before allocating an eligible replacement.
- Records lock, unlock, policy, approval, and reassignment events in the audit and outbox transaction.

### Module 08: Physical Answer Script Receiving

This module records the controlled arrival of physical answer-script consignments.

- Registers dispatch manifests, source centres, carriers, tracking references, expected packets, and expected script counts.
- Receives packets and bundles with operator, time, location, seal, and condition evidence.
- Reconciles expected and received quantities and raises shortage, excess, damage, seal, or manifest exceptions.
- Supports acknowledgement and resolution of operational alerts and dual confirmation for sensitive receiving actions.
- Hands verified packets and bundles to the custody module for script registration.

The guided demo route follows three separate workstations. **Script receiving** prepares a bundle with subject-specific packet manifests and dispatches it (or marks it for on-site scanning). **Chain of custody** scans the arriving bundle and then each packet inside it; on-site bundles skip the transport receipt but still require packet scans. **Digitization** opens a received packet by barcode and uploads one script's front page and answer pages at a time. Each packet has one subject and an expected booklet QR list. Admins can create a **Bundle preparer**, **Bundle and packet receiver**, or **Scan operator** in **Access governance > Access > Add user**. Each role sees only its workstation and is blocked from the others at the API; university administrators retain all three screens.

For the demo, barcode scans are typed into the matching fields and script scanning uses page-image uploads. On upload, the first page is read for a QR and the form's ten-column OMR USN grid (digit-only and letter-only columns). The QR must match the selected packet manifest; that packet determines the paper/subject. The USN is sent only to the isolated encrypted identity service. If recognition fails, the scan operator can enter the QR and USN from a valid cover image when `DEMO_MANUAL_RECOGNITION_ENABLED=true`; a readable QR must still match, packet membership is enforced, and the manual action is audited without exposing the USN. This fallback defaults to disabled outside the Docker demo and is not a production identity-verification method. This reader is calibrated to the supplied cover template; each different university form needs its own validated template before live use. After successful upload, guided scripts automatically mask page 1 and preserve answer pages, then appear in **Anonymization** for preview or one-step return for remasking. There is no separate identity registration or multi-person mask approval in this guided path. Exceptional candidate identity resolution remains separately protected. The guided demo path is enabled by `DEMO_MANUAL_INTAKE_ENABLED=true` in Docker and defaults to disabled outside Docker.

### Module 09: Script Identification, Barcode and Chain of Custody

This module gives each script an opaque operational identity and records every physical or digital handoff.

- Registers primary, supplementary, bundle, and centre barcodes against a system-generated opaque script code.
- Detects duplicate, missing, excess, or conflicting barcodes during reconciliation.
- Moves scripts only through the defined custody state machine, with actor, timestamp, location, and reason captured for every transition.
- Requires separate request and authorization for controlled transfers between locations or teams.
- Provides a complete custody timeline from receiving through scanning, masking, storage, allocation, evaluation, and archive.

### Module 10: High-Speed Scanning

This module operates the controlled scanner intake pipeline for physical answer scripts.

- Registers scanner devices, capabilities, location, health, maintenance state, and heartbeat activity.
- Creates barcode-bound scan batches and assigns jobs to available devices through a secure pull workflow.
- Accepts page manifests with sequence, barcode, size, MIME type, checksum, duplex, and scanner metadata.
- Detects duplicate pages, missing sequence numbers, unexpected barcodes, and device or batch failures.
- Tracks throughput, completion, failure, cancellation, and maintenance without routing image bytes through Django.

### Module 11: Scan Processing and Script Quality Control

This module turns raw scans into immutable evaluation-ready page masters.

- Applies versioned profiles for orientation, deskew, crop, grayscale, contrast, denoise, and blank-page analysis.
- Sends storage object references to the isolated storage gateway, which processes bytes and returns only metrics and digests.
- Produces immutable processed assets with source lineage and deterministic checksums.
- Scores blur, contrast, blankness, skew, cropping, and page completeness against configured thresholds.
- Opens operator exceptions for failed quality controls and supports reviewed reprocessing with retained history.

### Module 12: Script Anonymization and Identity Separation

This module removes candidate-identifying information before a script reaches evaluators.

- Runs scripts through masking preparation, independent review, masking, verification, and release stages.
- Produces anonymized master and evaluation renditions while preserving the protected raw scan separately.
- Requires different operators for sensitive masking and verification steps.
- Stores candidate-to-script identity links only in the isolated encrypted identity service; evaluation-core contains no candidate PII.
- Allows identity resolution only through two independent approvals and a short-lived, one-use authorization.

### Module 13: Digital Script Repository

This module manages the secure storage lifecycle of scanned and anonymized script files.

- Issues short-lived signed upload URLs so files travel directly between the browser and storage gateway instead of passing through Django.
- Finalizes uploads using verified checksum, MIME type, size, page, rendition, and version metadata.
- Maintains raw scans, immutable anonymized masters, evaluation copies, and versioned derived assets.
- Encrypts every object and writes matching primary, replica, and backup copies; archive completion is accepted only after all copies are verified.
- Supports secure deletion only for permitted non-master assets and removes every replicated copy when deletion is authorized.

### Module 14: Digital Evaluation Viewer

This is the evaluator's secure workspace for reading and marking allocated scripts.

- Opens only scripts assigned to the authenticated evaluator and obtains encrypted pages through signed URLs valid for no more than five minutes.
- Supports normal and low-bandwidth page delivery without exposing storage credentials or candidate identity.
- Tracks assignment start, viewing progress, page navigation, question marks, totals, annotations, and submission state.
- Uses optimistic locking and idempotent submission to protect against duplicate or concurrent writes.
- Makes question marks and annotations append-only after submission to preserve the evaluation record.

### Module 15: Digital Annotation

This module provides examiner markup without altering the underlying script image.

- Supports ticks, crosses, underlines, highlights, circles, rectangles, arrows, and page or question comments.
- Stores normalized geometry, style, page, question, actor, and sequence as separate annotation events.
- Implements undo as a new append-only event so earlier examiner actions remain reconstructable.
- Requires the evaluator's active assignment lock and evaluation version for every annotation write.
- Prevents annotation changes after submission while retaining the complete annotation history.

### Module 16: Question-Wise Evaluation

This module captures structured marks and validates the examiner's complete marking record.

- Records one current result per question through immutable superseding mark revisions.
- Supports evaluated, unattempted, invalid, absent, and review outcomes plus bonus, grace, penalty, and moderation adjustments.
- Enforces question maximums, permitted step marks, rubric criteria, and mandatory examiner confirmations.
- Recalculates totals from the latest question revisions and rejects incomplete or invalid submission.
- Uses optimistic evaluation versions and the active exclusive assignment lock on every write.

### Module 17: Marking Scheme and Rubrics

This module defines the approved instructions examiners must apply when awarding marks.

- Creates versioned paper schemes with general guidance, examiner instructions, model answers, and question criteria.
- Moves schemes through draft, review, approval, and frozen states using a controlled state machine.
- Requires independent approval, records clarification responses, and produces a digest of the frozen content.
- Supports mandatory evaluator acknowledgement of the exact frozen version before marking begins.
- Prevents frozen criteria from being silently edited or replaced during an active evaluation round.

### Module 18: Evaluation Workflow and Guided Operations

This module guides an assignment from first open to an idempotent, auditable submission.

- Maintains ready, draft, review, submitted, moderation, revaluation, finalized, and expired workflow states.
- Saves monotonic draft snapshots with page, question, checksum, expiry, and permitted UI state.
- Restores the latest draft sequence across sessions and coordinates assignment versions during autosave.
- Supports deadline-extension requests with independent approval and immutable decision history.
- Submits the evaluation, locks its valuation result, updates the assignment and custody state, releases the lock, and writes audit/outbox evidence in one database transaction.
- Recovers a previously submitted evaluation that has no valuation result, making retries safe after an interrupted client or older permission failure.

### Module 23: Multi-Valuation Engine

This module coordinates independent first, second, and third valuations without examiner leakage.

- Locks an immutable valuation result from each submitted evaluation using an idempotent finalize operation.
- Paper configuration sets one, two, or three required independent rounds. One-round papers propose the first score as the final mark unless an optional score trigger is configured; scores strictly above that trigger require an independent second round.
- Keeps previous-round examiner identity, marks, totals, and comparison data hidden from later evaluators.
- Compares question and total differences only after the required rounds are finalized.
- Applies the paper's configured tolerance to accept aligned valuations or open a discrepancy case.
- Allows the next round to be allocated only after the prior result is locked. Three-round papers require all three rounds; aligned scores follow the configured final-mark rule, while threshold breaches enter reconciliation.
- The optional score trigger is separate from the difference threshold: configure it under **Exam configuration → Papers → Round 2 if score exceeds** for a one-round paper. Frozen-paper changes use the existing governed change workflow.
- For existing one-round submissions made before this policy, preview missing final-mark proposals with `docker compose exec backend python manage.py reconcile_single_round_marks`; add them with `docker compose exec backend python manage.py reconcile_single_round_marks --apply`. The command is idempotent and does not approve or lock marks.

### Module 25: Discrepancy and Reconciliation Management

This module resolves valuations that exceed the allowed difference.

- Creates a case containing threshold breaches and question-level differences from the valuation comparison.
- Routes cases to moderator or chief examiner roles through explicit state transitions.
- Requests clarification from a specific valuation round while exposing only that evaluator's own request.
- Supports automatic, rule-based, manual, third-valuation, and controlled override resolutions.
- Requires an independent approval before the reconciled mark can become authoritative.

### Module 28: Evaluation Completion and Final Mark Control

This module proves an evaluation is complete and controls what may happen after the final mark is locked.

- Checks mandatory questions, required valuation rounds, moderation, discrepancies, revaluation, scan exceptions, integrity alerts, and final-mark checksum status.
- Captures the examiner declaration and creates a tamper-evident signature digest over the final mark and declaration.
- Separates proposal, approval, lock, result release, and post-lock authorization responsibilities.
- Makes locked final marks immutable; post-lock requests record a proposed superseding change rather than overwriting evidence.
- Releases a result only after a signed completion record and an independently approved, unexpired authorization are consumed.

### Module 24: Moderation

- Configures paper-level percentage, failed-script, high-score, low-score, and mandatory sampling policies.
- Selects deterministic samples from locked valuation results so repeated sampling does not create duplicate cases.
- Routes sampled, assigned, review, decided, and approved states using optimistic locking.
- Stores the original mark, fresh review snapshot, adjustment, and reason while requiring independent final approval.

### Module 27: Revaluation

- Accepts full-script or selected-question requests using an opaque student identity reference.
- Verifies that the original final mark is locked and preserves it as an immutable snapshot.
- Enforces an independent evaluator who did not participate in an earlier valuation.
- Calculates best-mark, average-mark, or regulation-threshold outcomes and retains the full difference history.

### Module 29: Remote Evaluation Security

- Runs a consented preflight that verifies webcam readiness, fullscreen entry, known media devices, and the connected-display count where the browser exposes it.
- Adds a live camera indicator and dynamic script/session watermark while blocking copy, cut, context-menu, save, and print shortcuts.
- Pauses marking on tab hiding, fullscreen exit, webcam interruption or obstruction, media-device changes, multiple displays, heartbeat loss, sustained face absence, or multiple faces.
- Uses browser-native face detection only as an optional in-memory signal; it stores no face templates, embeddings, or candidate identity.
- Buffers short webcam segments locally and uploads only incident-related clips through expiring signed URLs into encrypted, replicated storage.
- Creates a human review case and security alert for critical detections; authorized reviewers can inspect verified clips, record a rationale, clear the event, or escalate it.
- Applies tenant-configurable heartbeat, face-absence, evidence-capture, and retention rules, and purges expired clips from primary, replica, and backup storage.
- Enforces the active secure-session ID on viewer, marking, annotation, draft, submit, and finalization APIs, so bypassing the browser controls does not expose marking operations.

Browser security has a deliberate boundary: standard web APIs can observe displays and camera/microphone device changes, but cannot reliably inventory phones, USB storage, or every peripheral. Full external-device enforcement requires a managed kiosk browser or a signed desktop posture agent; this browser implementation records only signals it can verify.

### Module 31: Live Evaluation Monitoring

- Records evaluator check-in, check-out, active time, idle time, role, session, and exceptions.
- Calculates live assignments, remaining scripts, online evaluators, throughput, and near-deadline risk.
- Keeps monitoring data tenant-scoped and tied to real assignment and examination-session records.

### Module 32: Productivity and Workload Management

- Derives completed work, average handling time, scripts per hour, capacity, projected days, and workload risk.
- Separates pending, priority, moderation, discrepancy, revaluation, rescan, and security-risk queues.
- Routes rebalance, prioritize, and reallocate proposals through independent approval before execution.

### Module 33: Issue Management

- Captures technical, academic, script, scanner, access, and support issues with paper and question context.
- Classifies priority, detects likely duplicates, and calculates an SLA deadline.
- Supports assignment, escalation, resolution, confirmation, and reopening through versioned transitions.
- Locks confirmed resolutions and supports linked operational knowledge articles.

### Module 34: Runtime Recovery

- Records service failures with severity, last confirmed state, recovery point, and diagnostic details.
- Supports retrying, degraded operation, recovered, and failed states with retry counts.
- Exposes database, storage, queue, session, and incident health from actual platform records.

### Module 35: Notification Management

- Creates tenant-scoped in-app, push, email, and SMS delivery records.
- Tracks queued, delivered, failed, retried, acknowledged, and escalated states.
- Supports mandatory acknowledgement, delivery attempts, errors, and escalation deadlines.

### Module 36: Evaluation Centres and Camps

- Manages centre location, capacity, workstations, scanner references, schedules, supervisors, and security controls.
- Records scanner, workstation, network, power, secure-LAN, operator, and seat-plan readiness.
- Prevents activation without a successful readiness assessment.
- Operates planned, active, and closed evaluation camps with evaluator rosters, incidents, and performance evidence.

### Module 37: Offline and Low-Bandwidth Continuity

- Delivers lower-bandwidth signed page renditions through the secure viewer.
- Detects browser connectivity and queues only page-navigation progress during an interruption.
- Retries queued progress against the latest optimistic version when connectivity returns.
- Never stores marks, script images, identity data, authentication material, or storage URLs in browser persistence.

### Module 38: Evaluator Remuneration

- Defines paper or centre rates per script, page, question, moderation, revaluation, and configured slabs.
- Calculates payable units only from submitted assignments with verified attendance.
- Applies minimums, maximums, bonuses, and tax deductions with decimal-safe arithmetic.
- Requires independent approval before payment and tracks payment reference and reconciliation.

### Module 39: Student Script Services

- Accepts copy or revaluation requests using an opaque identity-service reference.
- Verifies finalized custody, locked final mark, and masked evaluation assets before approval.
- Provides watermarked, non-downloadable page access through signed URLs valid for five minutes.
- Tracks approval, expiry, availability, and access count without placing candidate PII in evaluation-core.

### Module 42: Audit and Forensics

- Reconstructs script, assignment, evaluation, and custody timelines from append-only evidence.
- Builds purpose-bound evidence packages for audits, disputes, grievances, RTI, or legal review.
- Seals each manifest with a SHA-256 digest and records the sealing actor and event count.

### Module 46: Integration Management

- Registers ERP and external endpoints using vault secret references rather than stored credentials.
- Queues locked final marks with a payload digest and idempotency key.
- Records sent, acknowledged, partial, rejected, failed, reconciled, and confirmed states.
- Compares ERP acknowledgements with the authoritative mark and checksum before confirmation.

### Module 48: Interface Internationalization

- Provides English, Kannada, Hindi, Tamil, Telugu, Malayalam, Marathi, Gujarati, Bengali, and Urdu choices.
- Persists the selected locale per tenant and user and publishes locale direction metadata for RTL rendering.
- Keeps additional institution-specific locale codes in the same preference contract.

### Module 49: Disaster Recovery

- Defines database and immutable-file replication configuration with RPO, RTO, regions, and clean recovery environment.
- Runs planned, running, verifying, passed, and failed recovery drills under step-up authentication.
- Captures recovery points, measurements, integrity checks, reports, and corrective actions.
- Prevents a recovery plan from becoming active until at least one drill has passed.

## Deferred modules

The following modules are intentionally not represented as implemented. They remain scheduled for later work.

- Phase 2: 05 Training, Sandbox and Certification; 30 Fraud, Investigation and Disciplinary Management; 43 Evaluation Analytics; 44 Statistical Analytics; 45 Executive Command Centre; 50 AI Governance and Kill Switch.
- External-data dependent: 19 AI Handwriting and Answer Intelligence; 20 AI Mark Recommendation; 21 AI Quality and Similarity; 22 Technical and Diagram Evaluation AI; 26 Integrity and Quality Intelligence; 51 Evaluator Copilot.

### Module 40: Data Security and Access Governance

This module provides operational security controls across the platform.

- Applies role-based authorization, tenant boundaries, step-up requirements, session controls, and data-loss-prevention response headers.
- Manages privileged-access requests with purpose, expiry, independent approval, and revocation.
- Supports time-limited emergency access while recording the requester, approver, reason, actions, and expiry.
- Tracks security incidents, assignments, status, severity, evidence, and resolution history.
- Exposes security posture and authentication history without weakening the underlying audit trail.

### Module 41: Script Integrity and Anti-Tampering

This module continuously proves that script assets and their operational history have not changed unexpectedly.

- Builds Ed25519-signed manifests over asset digests, metadata, custody history, and version lineage.
- Verifies manifests on demand and through a dedicated Docker integrity worker.
- Detects byte, metadata, custody, or version tampering without silently repairing the evidence.
- Creates integrity alerts and immutable evidence records for failed checks.
- Exposes verification status and signing controls to authorized digitization operators.

### Module 47: Multi-University and Enterprise Configuration

This module allows one deployment to serve multiple institutions while keeping their data separated.

- Provisions institutions with unique codes, domains, status, versioned settings, branding, and feature configuration.
- Manages user membership and institution-specific roles.
- Allows authorized users to switch institutions while binding their access session to the selected tenant.
- Applies tenant filters and authorization checks to every operational read and write.
- Gives platform administrators a cross-institution control plane without exposing one university's operational records to another.

## End-to-end workflow

1. The university and examination structure is created in Module 01.
2. Evaluators are registered in Module 02 and verified for subjects in Module 03.
3. Users authenticate through Module 04 under the institution selected by Module 47.
4. Physical consignments are received in Module 08 and registered into custody in Module 09.
5. Module 10 scans scripts and Module 11 creates quality-controlled immutable page masters in Module 13.
6. Module 41 signs and continuously verifies asset and custody integrity; Module 12 releases anonymized evaluation renditions.
7. Modules 06 and 07 allocate eligible evaluators and enforce assignment governance and exclusive locks.
8. Evaluators use Modules 14 through 18 to view, annotate, mark question by question, autosave, and submit against a frozen rubric.
9. Modules 23 and 25 compare blind valuation rounds and reconcile differences.
10. Modules 24, 27, and 28 moderate, revalue, verify, sign, and release the final result under independent authorization.
11. Modules 29 and 31 through 37 protect and monitor live evaluation operations, workload, issues, notifications, centres, and interrupted connectivity.
12. Modules 38 and 39 calculate evaluator remuneration and provide expiring student script services.
13. Modules 42, 46, 48, and 49 supply forensic evidence, controlled result integration, interface locale preferences, and tested recovery plans. Module 40 applies security governance throughout, while each domain write creates audit and outbox records in the same transaction.

## Docker development

```bash
cp .env.example .env
docker compose up --build
```

Open `http://localhost:3000`. Compose starts PostgreSQL, evaluation-core, identity-service, encrypted storage, the outbox publisher, the continuous integrity verifier, the secure-session heartbeat/retention worker, and the frontend. Named volumes preserve all state across restarts:

### Multi-university control plane

The platform administrator opens the Super Admin control plane at the platform host and can provision, suspend, reactivate, configure, and enter isolated university tenants. Provisioning creates the root institution, tenant account, managed domain, module entitlements, storage quota, first administrator membership, and audit/outbox evidence in one transaction. A generated temporary password is displayed once and must be changed before any operational API can be used.

Local managed tenant URLs use `<university>.localhost:3000`, for example `northbridge.localhost:3000`. For production at `abc.com`, configure:

```dotenv
TENANT_BASE_DOMAIN=abc.com
PLATFORM_HOSTS=platform.abc.com
ENFORCE_TENANT_DOMAINS=true
DJANGO_ALLOWED_HOSTS=.abc.com,backend
CSRF_TRUSTED_ORIGINS=https://*.abc.com
COOKIE_SECURE=true
APP_BASE_URL=https://platform.abc.com
```

Create wildcard DNS and TLS for `*.abc.com`; a university provisioned with slug `northbridge` then receives `https://northbridge.abc.com`. Optional university-owned hostnames are registered as pending custom domains and expose a unique DNS TXT ownership challenge. Hostname resolution happens before authorization, domain-bound users cannot cross into another tenant, and disabled modules are rejected at both navigation and API boundaries.

### User access, evaluator onboarding, and notifications

University administrators manage access from **Access governance > User access**. **Add user** creates or reuses an identity, assigns a tenant role, limits the account to selected modules, and displays a temporary password once. An evaluator account is restricted to the Evaluation module. Existing memberships can be edited or suspended from the same screen, and module grants are enforced by both frontend navigation and backend middleware.

For guided intake, assign **Bundle dispatch operator** to Script receiving, **Bundle and packet receiver** to Chain of custody, or **Script scanning operator** to Digitization. Each account is confined to its own desk. **Operations supervisor** can see the intake progress dashboard and work across all three desks, but cannot access evaluation, security, or the separate Live operations module. These role presets are fixed in both the access form and API; supervisors can advance records through the audited workflow, not rewrite completed history.

**Evaluator master > Register evaluator** can create the evaluator profile and linked login together. The issued username and one-time temporary password are shown after the transaction succeeds. New assignment and redistribution events create durable in-app notifications for the linked evaluator; the header notification centre opens the assigned Evaluation desk and records read or acknowledgement state.

For local development only, Docker sets `DEMO_SKIP_EVALUATOR_FACE_VERIFICATION=true`. Evaluators can enter a secure evaluation session without face enrollment or a face-match prompt; webcam preflight, proctoring, session authorization, and assignment locks remain enabled. Set this variable to `false` to restore face verification. The bypass is disabled whenever `DJANGO_DEBUG=false`, regardless of the variable.

Platform administrators configure university-specific fields from **University management > Configure custom form fields**. Field definitions are tenant-scoped, ordered, versioned, audited, and support text, long text, number, date, select, and checkbox inputs. The configured fields are validated and persisted end to end for User Access, Evaluator Profile, Institution, Exam Session, Paper, Dispatch, Answer Script, Evaluator Allocation, Marking Scheme, and Notification creation forms.

When scanner hardware is unavailable, use **Digitization > Upload answer paper**. The workflow accepts ordered JPEG, PNG, or WebP page images for a registered script, uploads them through signed storage URLs, finalizes every object, and moves the script to Scanned only after all pages succeed.

- `postgres_data`: operational data, audit, outbox, policies, and metadata
- `identity_data`: separately stored encrypted candidate identity data
- `script_storage`: encrypted primary script objects
- `script_replica`: encrypted replica objects
- `script_backup`: encrypted backup objects

Use `docker compose down` to stop without deleting data. Do not add `-v` unless the stored development data should be erased.

The local seed credentials are:

| Role | Email | Password |
|---|---|---|
| University administrator | `admin@admiezo.local` | `ChangeMe123!` |
| Evaluator with assignments | `evaluator1043@admiezo.local` | `ChangeMe123!` |
| Platform administrator | `platform@admiezo.local` | `ChangeMe123!` |

## Native development

Install dependencies and initialize the databases:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python backend/manage.py migrate
.venv/bin/python backend/manage.py bootstrap_demo
.venv/bin/python identity_service/manage.py migrate
cd frontend && npm ci && cd ..
```

Run these four processes from the repository root:

```bash
.venv/bin/python backend/manage.py runserver 127.0.0.1:8000
.venv/bin/python identity_service/manage.py runserver 127.0.0.1:8100
STORAGE_ROOT="$PWD/var/storage" STORAGE_REPLICA_ROOT="$PWD/var/storage-replica" STORAGE_BACKUP_ROOT="$PWD/var/storage-backup" STORAGE_SIGNING_KEY=local-storage-signing-key-change-me STORAGE_ENCRYPTION_KEY=local-storage-encryption-key-change-me .venv/bin/uvicorn main:app --app-dir storage_gateway --host 127.0.0.1 --port 9000
cd frontend && npm run dev -- --hostname 0.0.0.0 --port 3000
```

The native SQLite databases and `var/` storage directories are persistent local development data. PostgreSQL and object storage can be introduced later without changing API contracts.

API documentation is at `http://localhost:8000/api/docs`.

## Verification

```bash
cd backend && ../.venv/bin/python manage.py test && ../.venv/bin/ruff check . ../identity_service ../storage_gateway
cd ../identity_service && ../.venv/bin/python manage.py test
cd .. && .venv/bin/pytest -q storage_gateway/test_main.py
cd frontend && npm run lint && npm run build && npm run test:e2e
cd .. && docker compose config --quiet
```

## Server deployment

Local defaults are intentionally rejected when `DJANGO_DEBUG=false`. Before a shared deployment:

1. Use independent PostgreSQL databases for evaluation-core and identity-service with `DB_SSLMODE=require` and `IDENTITY_DB_SSLMODE=require` or stronger.
2. Generate unique secrets of at least 32 characters for every Django, encryption, signing, identity authorization, storage, and outbox credential.
3. Set `COOKIE_SECURE=true`, an HTTPS `APP_BASE_URL`, exact allowed hosts, and exact CSRF trusted origins.
4. Set `LOAD_DEMO_DATA=false`, `STORAGE_ENVIRONMENT=production`, and disable storage demo seeding.
5. Configure `OUTBOX_MODE=webhook` with an HTTPS destination. Events carry an `Idempotency-Key` header and retry until acknowledged.
6. Configure the WAF, DDoS, IDS, SIEM/security monitoring, vulnerability scanner, secrets manager, and KMS provider variables shown in `.env.example`.
7. Put TLS termination and the frontend behind the chosen reverse proxy. Keep evaluation-core, identity-service, PostgreSQL, and storage private.

The storage gateway provides local encrypted redundancy for development. On the server, mount primary, replica, and backup paths on independent durable volumes or replace the gateway implementation with object storage while preserving its signed URL contract.
