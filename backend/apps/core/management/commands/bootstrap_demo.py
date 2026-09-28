import os
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.utils import timezone

from apps.allocation.models import Assignment, AssignmentHistory
from apps.assignment.models import AssignmentGovernancePolicy
from apps.configuration.models import AcademicYear, ExamSession, Paper, Programme, Question, Subject, Term, Regulation
from apps.custody.models import CustodyEvent, Script
from apps.evaluators.models import Evaluator, Expertise
from apps.eligibility.models import EligibilityRecord, VerificationApproval, VerificationCase
from apps.phase4.models import (
    CentreProfile,
    CentreReadiness,
    EvaluationCamp,
    IntegrationEndpoint,
    LocalePreference,
    ModerationPolicy,
    NotificationDelivery,
    OperationalIssue,
    PresenceSecurityEvent,
    ProctoringEvidence,
    ProctoringReview,
    RecoveryDrill,
    RecoveryPlan,
    RemunerationRule,
    RuntimeIncident,
    SecureEvaluationSession,
)
from apps.receiving.models import Dispatch, Packet, ReceivingException
from apps.repository.models import ScriptAsset
from apps.rubrics.models import InstructionAcknowledgement, MarkingScheme, RubricCriterion
from apps.rubrics.services import content_digest
from apps.security.models import SecurityPolicy
from apps.scan_processing.models import ProcessingProfile, ProcessingRun
from apps.scanning.models import ScanBatch, ScanJob, ScannerDevice
from apps.tenancy.models import Institution, Membership, TenantAccount, TenantDomain
from apps.tenancy.services import DEFAULT_MODULES


class Command(BaseCommand):
    help = "Create an idempotent local ADMIEZO workspace with realistic operational records."

    @transaction.atomic
    def handle(self, *args, **options):
        email = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "admin@admiezo.local").lower()
        password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "ChangeMe123!")
        user, created = User.objects.get_or_create(username=email, defaults={"email": email, "first_name": "Asha", "last_name": "Menon", "is_staff": True})
        if created or not user.has_usable_password():
            user.set_password(password)
            user.save()
        institution, _ = Institution.objects.get_or_create(
            code="northbridge-university",
            defaults={"name": "Northbridge University", "kind": Institution.Kind.UNIVERSITY, "policy": {"mfa_required": False, "session_minutes": 30}},
        )
        tenant_id = institution.tenant_id
        institution_policy = dict(institution.policy or {})
        if institution_policy.get("mfa_required") is not False:
            institution_policy["mfa_required"] = False
            institution.policy = institution_policy
            institution.save(update_fields=["policy", "updated_at"])
        SecurityPolicy.objects.update_or_create(tenant_id=tenant_id, defaults={"require_mfa": False})
        account, _ = TenantAccount.objects.get_or_create(
            root_institution=institution,
            defaults={"slug": "northbridge", "status": TenantAccount.Status.ACTIVE, "plan": TenantAccount.Plan.ENTERPRISE, "enabled_modules": DEFAULT_MODULES},
        )
        TenantDomain.objects.get_or_create(
            hostname=f"{account.slug}.{settings.TENANT_BASE_DOMAIN}",
            defaults={"tenant_account": account, "kind": TenantDomain.Kind.MANAGED, "status": TenantDomain.Status.ACTIVE, "is_primary": True, "verified_at": timezone.now()},
        )
        admin_membership, _ = Membership.objects.get_or_create(
            user=user,
            institution=institution,
            defaults={"role": Membership.Role.UNIVERSITY_ADMIN, "permissions": ["*"], "enabled_modules": account.enabled_modules},
        )
        if admin_membership.enabled_modules != account.enabled_modules:
            admin_membership.enabled_modules = list(account.enabled_modules)
            admin_membership.save(update_fields=["enabled_modules", "updated_at"])
        operators = {}
        for account_email, first_name, last_name, role in [
            ("platform@admiezo.local", "Platform", "Administrator", Membership.Role.PLATFORM_ADMIN),
            ("controller@admiezo.local", "Exam", "Controller", Membership.Role.EXAM_CONTROLLER),
            ("reviewer@admiezo.local", "Mask", "Reviewer", Membership.Role.EXAM_CONTROLLER),
            ("verifier@admiezo.local", "Mask", "Verifier", Membership.Role.EXAM_CONTROLLER),
            ("auditor@admiezo.local", "Internal", "Auditor", Membership.Role.AUDITOR),
        ]:
            operator, operator_created = User.objects.get_or_create(username=account_email, defaults={"email": account_email, "first_name": first_name, "last_name": last_name})
            if operator_created or not operator.has_usable_password():
                operator.set_password(password)
                operator.save()
            operator_membership, _ = Membership.objects.get_or_create(
                user=operator,
                institution=institution,
                defaults={"role": role, "enabled_modules": account.enabled_modules if role == Membership.Role.PLATFORM_ADMIN else []},
            )
            if role == Membership.Role.PLATFORM_ADMIN and operator_membership.enabled_modules != account.enabled_modules:
                operator_membership.enabled_modules = list(account.enabled_modules)
                operator_membership.save(update_fields=["enabled_modules", "updated_at"])
            operators[account_email] = operator
        year, _ = AcademicYear.objects.get_or_create(tenant_id=tenant_id, label="2026-27", defaults={"starts_on": date(2026, 7, 1), "ends_on": date(2027, 6, 30)})
        term, _ = Term.objects.get_or_create(tenant_id=tenant_id, academic_year=year, name="Odd semester", defaults={"sequence": 1, "starts_on": year.starts_on, "ends_on": year.ends_on})
        session, _ = ExamSession.objects.get_or_create(
            tenant_id=tenant_id,
            academic_year=year,
            name="November 2026 End Semester",
            defaults={"term": "Odd semester", "term_record": term, "evaluation_starts_at": timezone.now() - timedelta(days=2), "evaluation_ends_at": timezone.now() + timedelta(days=18), "status": ExamSession.Status.ACTIVE},
        )
        if session.term_record_id != term.id:
            session.term_record = term
            session.save(update_fields=["term_record", "updated_at"])
        regulation, _ = Regulation.objects.get_or_create(tenant_id=tenant_id, code="R-2025", defaults={"title": "Regulation 2025", "effective_from": date(2025, 1, 1)})
        programme, _ = Programme.objects.get_or_create(tenant_id=tenant_id, code="BTECH-CSE", defaults={"name": "B.Tech Computer Science", "regulation": "R-2025", "regulation_record": regulation})
        if programme.regulation_record_id != regulation.id:
            programme.regulation_record = regulation
            programme.save(update_fields=["regulation_record", "updated_at"])
        subjects = [
            ("CS401", "Distributed Systems", 7),
            ("CS402", "Applied Machine Learning", 7),
            ("CS301", "Database Management Systems", 5),
            ("MA301", "Probability and Statistics", 5),
        ]
        papers = []
        for index, (code, name, semester) in enumerate(subjects):
            subject, _ = Subject.objects.get_or_create(tenant_id=tenant_id, programme=programme, code=code, defaults={"name": name, "semester": semester})
            if str(session.id) not in subject.session_ids:
                subject.session_ids = [*subject.session_ids, str(session.id)]
                subject.save(update_fields=["session_ids", "updated_at"])
            paper, _ = Paper.objects.get_or_create(
                tenant_id=tenant_id,
                session=session,
                subject=subject,
                code=f"{code}-A",
                defaults={
                    "title": name,
                    "max_marks": Decimal("100"),
                    "pass_marks": Decimal("40"),
                    "valuation_rounds": 2 if index < 2 else 1,
                    "discrepancy_threshold": Decimal("15"),
                    "moderation_required": index == 0,
                    "status": Paper.Status.FROZEN if index < 3 else Paper.Status.REVIEW,
                    "submitted_by_id": user.id,
                    "submitted_at": timezone.now() - timedelta(days=5),
                    "approved_by_id": operators["controller@admiezo.local"].id if index < 3 else None,
                    "frozen_by_id": operators["reviewer@admiezo.local"].id if index < 3 else None,
                    "frozen_at": timezone.now() if index < 3 else None,
                },
            )
            actor_updates = []
            if paper.status in {Paper.Status.REVIEW, Paper.Status.APPROVED, Paper.Status.FROZEN}:
                if paper.submitted_by_id is None:
                    paper.submitted_by_id = user.id
                    actor_updates.append("submitted_by_id")
                if paper.submitted_at is None:
                    paper.submitted_at = timezone.now() - timedelta(days=5)
                    actor_updates.append("submitted_at")
            if paper.status in {Paper.Status.APPROVED, Paper.Status.FROZEN} and paper.approved_by_id is None:
                paper.approved_by_id = operators["controller@admiezo.local"].id
                actor_updates.append("approved_by_id")
            if paper.status == Paper.Status.FROZEN:
                if paper.frozen_by_id is None:
                    paper.frozen_by_id = operators["reviewer@admiezo.local"].id
                    actor_updates.append("frozen_by_id")
                if paper.frozen_at is None:
                    paper.frozen_at = timezone.now()
                    actor_updates.append("frozen_at")
            if actor_updates:
                paper.save(update_fields=[*actor_updates, "updated_at"])
            if not paper.questions.exists():
                for position in range(1, 6):
                    Question.objects.create(tenant_id=tenant_id, paper=paper, number=f"Q{position}", max_marks=Decimal("20"), position=position)
            papers.append(paper)
        evaluators = []
        for index, name in enumerate(["Dr. Kavya Rao", "Prof. Imran Khan", "Dr. Neha Pillai", "Prof. Arjun Das", "Dr. Meera Iyer"]):
            evaluator_email = f"evaluator{1042 + index}@admiezo.local"
            evaluator, _ = Evaluator.objects.get_or_create(
                tenant_id=tenant_id,
                evaluator_code=f"EV-{1042 + index}",
                defaults={
                    "display_name": name,
                    "email": evaluator_email,
                    "mobile": f"+91990000{1042 + index}",
                    "employee_id": f"NBU-FAC-{1042 + index}",
                    "institution_name": "Northbridge University",
                    "department": "Computer Science",
                    "designation": "Associate Professor",
                    "qualification": "PhD",
                    "years_experience": 8 + index,
                    "status": Evaluator.Status.PENDING if index == 4 else Evaluator.Status.ACTIVE,
                    "daily_capacity": 18 + index,
                },
            )
            changed_fields = []
            if not evaluator.email:
                evaluator.email = evaluator_email
                changed_fields.append("email")
            if not evaluator.mobile:
                evaluator.mobile = f"+91990000{1042 + index}"
                changed_fields.append("mobile")
            if not evaluator.employee_id:
                evaluator.employee_id = f"NBU-FAC-{1042 + index}"
                changed_fields.append("employee_id")
            if changed_fields:
                evaluator.save(update_fields=[*changed_fields, "updated_at"])
            Expertise.objects.get_or_create(tenant_id=tenant_id, evaluator=evaluator, subject=papers[index % len(papers)].subject, defaults={"level": 4, "verified": evaluator.status == Evaluator.Status.ACTIVE})
            evaluator_user, evaluator_user_created = User.objects.get_or_create(username=evaluator_email, defaults={"email": evaluator_email, "first_name": name.split()[1], "last_name": name.split()[-1]})
            if evaluator_user_created or not evaluator_user.has_usable_password():
                evaluator_user.set_password(password)
                evaluator_user.save()
            Membership.objects.get_or_create(user=evaluator_user, institution=institution, defaults={"role": Membership.Role.EVALUATOR})
            if evaluator.user_id != evaluator_user.id:
                evaluator.user = evaluator_user
                evaluator.save(update_fields=["user", "updated_at"])
            evaluators.append(evaluator)
        all_checks = {key: True for key in ("official_id", "university_employee", "faculty", "mobile", "email", "institutional_email", "qualification", "experience", "institution", "department", "designation", "subject_expertise", "documents", "kyc")}
        for index, evaluator in enumerate(evaluators):
            if evaluator.status != Evaluator.Status.ACTIVE:
                continue
            verification, _ = VerificationCase.objects.update_or_create(tenant_id=tenant_id, evaluator=evaluator, defaults={"status": VerificationCase.Status.APPROVED, "checks": all_checks, "submitted_at": timezone.now() - timedelta(days=10), "submitted_by_id": user.id, "reviewed_at": timezone.now() - timedelta(days=9), "reviewer_id": operators["verifier@admiezo.local"].id, "expires_on": date(2027, 12, 31), "revalidation_due_on": date(2027, 12, 1), "required_approvals": 2, "fraud_signals": [], "notes": "Verified local development evaluator"})
            for level, approver in enumerate((operators["reviewer@admiezo.local"], operators["verifier@admiezo.local"]), 1):
                VerificationApproval.objects.get_or_create(tenant_id=tenant_id, verification=verification, level=level, defaults={"approver_id": approver.id, "decision": VerificationApproval.Decision.APPROVED, "notes": "Approved local development evaluator"})
            for offset in (0, 1):
                subject = papers[(index + offset) % len(papers)].subject
                expertise, _ = Expertise.objects.update_or_create(tenant_id=tenant_id, evaluator=evaluator, subject=subject, defaults={"level": 4 if offset == 0 else 3, "years_experience": evaluator.years_experience, "verified": True})
                EligibilityRecord.objects.update_or_create(tenant_id=tenant_id, evaluator=evaluator, subject=subject, defaults={"status": EligibilityRecord.Status.ELIGIBLE, "qualification_ok": True, "experience_ok": True, "institution_ok": True, "expertise_ok": expertise.verified, "has_conflict": False, "is_debarred": False, "is_blacklisted": False, "risk_reasons": [], "valid_from": date(2026, 7, 1), "expires_on": date(2027, 12, 31), "approved_by_id": user.id})
        for paper in papers:
            AssignmentGovernancePolicy.objects.get_or_create(
                tenant_id=tenant_id,
                paper=paper,
                defaults={"approval_required": True, "assignment_expiry_hours": 120, "lock_minutes": 30, "require_step_up_for_reassignment": True},
            )
            scheme, _ = MarkingScheme.objects.get_or_create(
                tenant_id=tenant_id,
                paper=paper,
                version=1,
                defaults={
                    "title": f"{paper.code} standard marking scheme",
                    "evaluation_guidelines": "Award marks against the published criterion evidence. Record zero-mark and unanswered outcomes explicitly.",
                    "examiner_instructions": "Evaluate every mandatory question independently. Confirm any exceptional or bonus award before submission.",
                    "partial_credit_rules": {"enabled": True, "minimum_increment": "0.5"},
                    "tolerance_rules": {"rounding": "0.5"},
                    "status": MarkingScheme.Status.FROZEN,
                    "created_by_id": user.id,
                    "approved_by_id": operators["controller@admiezo.local"].id,
                    "approved_at": timezone.now() - timedelta(days=4),
                    "frozen_by_id": operators["reviewer@admiezo.local"].id,
                    "frozen_at": timezone.now() - timedelta(days=3),
                },
            )
            for question in paper.questions.all():
                RubricCriterion.objects.get_or_create(
                    tenant_id=tenant_id,
                    scheme=scheme,
                    question=question,
                    code=f"{question.number}-CORE",
                    defaults={"description": f"Demonstrates the required concepts and reasoning for {question.number}.", "max_marks": question.max_marks, "step_marks": [5, 10, 15, 20], "partial_credit_rule": {"evidence_required": True}, "position": 1, "is_mandatory": True},
                )
            digest = content_digest(scheme)
            if scheme.content_digest != digest:
                scheme.content_digest = digest
                scheme.save(update_fields=["content_digest", "updated_at"])
            for evaluator in evaluators:
                if evaluator.status == Evaluator.Status.ACTIVE:
                    InstructionAcknowledgement.objects.get_or_create(tenant_id=tenant_id, scheme=scheme, evaluator=evaluator, clarification=None, defaults={"content_digest": digest, "acknowledged_at": timezone.now() - timedelta(days=2)})
        ScannerDevice.objects.update_or_create(
            tenant_id=tenant_id,
            code="SCAN-CENTRAL-01",
            defaults={"name": "Central duplex scanner", "location": "Digitization Hall A", "topology": ScannerDevice.Topology.CENTRAL, "capabilities": {"adf": True, "duplex": True, "dpi": [200, 300, 600], "maximum_batch_pages": 500}, "status": ScannerDevice.Status.ONLINE, "last_heartbeat_at": timezone.now(), "pages_per_minute": Decimal("72"), "pages_scanned_today": 1840, "failure_rate": Decimal("0.4"), "firmware_version": "4.8.2"},
        )
        ScannerDevice.objects.update_or_create(
            tenant_id=tenant_id,
            code="SCAN-DIST-01",
            defaults={"name": "Distributed intake scanner", "location": "Evaluation Centre B", "topology": ScannerDevice.Topology.DISTRIBUTED, "capabilities": {"adf": True, "duplex": True, "dpi": [200, 300]}, "status": ScannerDevice.Status.ONLINE, "last_heartbeat_at": timezone.now(), "pages_per_minute": Decimal("46"), "pages_scanned_today": 612, "failure_rate": Decimal("1.1"), "firmware_version": "3.12.0"},
        )
        profile, _ = ProcessingProfile.objects.get_or_create(
            tenant_id=tenant_id,
            code="EXAM-300DPI",
            version=1,
            defaults={"name": "Exam script quality profile", "configuration": {"resolution_dpi": 300, "minimum_quality_score": 65, "grayscale": True, "background_normalization": True, "noise_reduction": True, "contrast": 1.08}, "is_active": True},
        )
        # Keep the Command Centre empty until real Operations data is created.
        #
        # Older versions of this bootstrap command created demo dispatches,
        # scripts, repository assets, assignments, receiving exceptions and
        # scan batches. Those records made the Command Centre show non-zero
        # operational counts even before an administrator entered real data.
        #
        # Clean up only the records created by that old demo block. Do not
        # delete the institution, users, exam configuration, papers, evaluator
        # setup, or any unrelated real operational data.
        demo_dispatch_refs = [f"DSP-2026-{index:03d}" for index in range(1, 5)]
        demo_script_codes = [f"AS-{index:06d}" for index in range(1, 49)]
        demo_script_codes.extend(["AS-SCAN-DEMO-1", "AS-SCAN-DEMO-2"])

        replenished_script_ids = list(
            CustodyEvent.objects.filter(
                tenant_id=tenant_id,
                metadata__source="demo_replenishment",
            ).values_list("script_id", flat=True)
        )

        demo_script_ids = list(
            Script.objects.filter(
                tenant_id=tenant_id,
                script_code__in=demo_script_codes,
            ).values_list("id", flat=True)
        )
        demo_script_ids.extend(replenished_script_ids)
        demo_script_ids = list(dict.fromkeys(demo_script_ids))

        if demo_script_ids:
            for script_id in demo_script_ids:
                try:
                    # Keep all records for a script if another workflow protects it.
                    with transaction.atomic():
                        ProcessingRun.objects.filter(tenant_id=tenant_id, script_id=script_id).delete()
                        ScanJob.objects.filter(tenant_id=tenant_id, script_id=script_id).delete()
                        assignment_ids = Assignment.objects.filter(
                            tenant_id=tenant_id,
                            script_id=script_id,
                        ).values_list("id", flat=True)
                        AssignmentHistory.objects.filter(
                            tenant_id=tenant_id,
                            assignment_id__in=assignment_ids,
                        ).delete()
                        Assignment.objects.filter(tenant_id=tenant_id, script_id=script_id).delete()
                        ScriptAsset.objects.filter(tenant_id=tenant_id, script_id=script_id).delete()
                        CustodyEvent.objects.filter(tenant_id=tenant_id, script_id=script_id).delete()
                        Script.objects.filter(tenant_id=tenant_id, id=script_id).delete()
                except ProtectedError:
                    self.stdout.write(f"Preserved demo script {script_id}: referenced by another workflow")

        ScanBatch.objects.filter(
            tenant_id=tenant_id,
            reference__in=["SCAN-DEMO-QUEUED", "SCAN-DEMO-COMPLETE"],
        ).delete()

        demo_dispatch_ids = list(Dispatch.objects.filter(
            tenant_id=tenant_id,
            reference__in=demo_dispatch_refs,
        ).values_list("id", flat=True))
        for dispatch_id in demo_dispatch_ids:
            try:
                with transaction.atomic():
                    ReceivingException.objects.filter(tenant_id=tenant_id, dispatch_id=dispatch_id).delete()
                    Packet.objects.filter(tenant_id=tenant_id, dispatch_id=dispatch_id).delete()
                    Dispatch.objects.filter(tenant_id=tenant_id, id=dispatch_id).delete()
            except ProtectedError:
                self.stdout.write(f"Preserved demo dispatch {dispatch_id}: referenced by another workflow")

        for paper in papers:
            ModerationPolicy.objects.update_or_create(
                tenant_id=tenant_id,
                paper=paper,
                defaults={"sample_percentage": Decimal("10"), "sampling_modes": ["percentage_random", "failed_script", "high_score"], "high_score_threshold": Decimal("85"), "mandatory": paper.moderation_required},
            )
        RemunerationRule.objects.update_or_create(
            tenant_id=tenant_id,
            paper=papers[0],
            centre=None,
            defaults={"per_script": Decimal("28"), "per_page": Decimal("0.50"), "per_question": Decimal("1.25"), "moderator_rate": Decimal("45"), "revaluation_rate": Decimal("90"), "slabs": [{"minimum": 100, "bonus": 500}], "minimum_payment": Decimal("500"), "maximum_payment": Decimal("25000"), "tax_percentage": Decimal("10")},
        )
        # Keep Live Operations empty until real operational activity creates records.
        # Remove only the known demo centre/readiness/camp records from older bootstrap runs.
        demo_centre_ids = list(CentreProfile.objects.filter(
            tenant_id=tenant_id,
            code="NBU-CENTRAL",
        ).values_list("id", flat=True))
        for centre_id in demo_centre_ids:
            try:
                with transaction.atomic():
                    EvaluationCamp.objects.filter(
                        tenant_id=tenant_id,
                        centre_id=centre_id,
                        name="November 2026 Central Evaluation Camp",
                    ).delete()
                    CentreReadiness.objects.filter(tenant_id=tenant_id, centre_id=centre_id).delete()
                    CentreProfile.objects.filter(tenant_id=tenant_id, id=centre_id).delete()
            except ProtectedError:
                self.stdout.write(f"Preserved demo centre {centre_id}: referenced by another workflow")

        IntegrationEndpoint.objects.update_or_create(
            tenant_id=tenant_id,
            name="University ERP Result Gateway",
            defaults={"kind": "erp_results", "base_url": "https://erp.example.edu/api/results", "authentication": "oauth2_client", "secret_reference": "vault://admiezo/erp/result-client", "rate_limit_per_minute": 120, "webhook_events": ["result.acknowledged", "result.rejected"], "status": IntegrationEndpoint.Status.ACTIVE},
        )
        plan, _ = RecoveryPlan.objects.update_or_create(
            tenant_id=tenant_id,
            name="Primary regional recovery plan",
            defaults={"regions": ["primary-local", "secondary-dev"], "database_replication": {"mode": "point-in-time", "verified": True}, "file_replication": {"mode": "immutable-copy", "verified": True}, "rpo_minutes": 15, "rto_minutes": 60, "clean_environment": "docker-recovery", "status": "approved"},
        )
        RecoveryDrill.objects.get_or_create(tenant_id=tenant_id, plan=plan, drill_type="database-and-object-restore", defaults={"recovery_point": {"type": "latest_verified"}, "requested_by_id": user.id})
        LocalePreference.objects.update_or_create(tenant_id=tenant_id, user_id=user.id, defaults={"locale": "en", "additional_locales": []})
        # Remove only the known Live Operations demo records from older bootstrap runs.
        RuntimeIncident.objects.filter(
            tenant_id=tenant_id,
            service="storage-gateway",
            category="transient_latency",
        ).delete()
        OperationalIssue.objects.filter(
            tenant_id=tenant_id,
            title="scanner queue delayed",
        ).delete()
        NotificationDelivery.objects.filter(
            tenant_id=tenant_id,
            user_id=user.id,
            title="Daily readiness completed",
        ).delete()
        self.stdout.write(self.style.SUCCESS(f"ADMIEZO workspace ready for {email}"))
