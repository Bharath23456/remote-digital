import os
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.allocation.models import Assignment
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
    RecoveryDrill,
    RecoveryPlan,
    RemunerationRule,
    RuntimeIncident,
)
from apps.receiving.models import Dispatch, Packet, ReceivingException
from apps.repository.models import ScriptAsset
from apps.repository.storage import create_demo_page
from apps.rubrics.models import InstructionAcknowledgement, MarkingScheme, RubricCriterion
from apps.rubrics.services import content_digest
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
            defaults={"name": "Northbridge University", "kind": Institution.Kind.UNIVERSITY, "policy": {"mfa_required": True, "session_minutes": 30}},
        )
        tenant_id = institution.tenant_id
        account, _ = TenantAccount.objects.get_or_create(
            root_institution=institution,
            defaults={"slug": "northbridge", "status": TenantAccount.Status.ACTIVE, "plan": TenantAccount.Plan.ENTERPRISE, "enabled_modules": DEFAULT_MODULES},
        )
        TenantDomain.objects.get_or_create(
            hostname=f"{account.slug}.{settings.TENANT_BASE_DOMAIN}",
            defaults={"tenant_account": account, "kind": TenantDomain.Kind.MANAGED, "status": TenantDomain.Status.ACTIVE, "is_primary": True, "verified_at": timezone.now()},
        )
        Membership.objects.get_or_create(user=user, institution=institution, defaults={"role": Membership.Role.UNIVERSITY_ADMIN, "permissions": ["*"]})
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
            Membership.objects.get_or_create(user=operator, institution=institution, defaults={"role": role})
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
        script_number = 1
        for paper_index, paper in enumerate(papers):
            expected = [320, 286, 244, 198][paper_index]
            received = expected if paper_index < 2 else expected - (3 if paper_index == 2 else 8)
            dispatch, _ = Dispatch.objects.get_or_create(
                tenant_id=tenant_id,
                reference=f"DSP-2026-{paper_index + 1:03d}",
                defaults={
                    "paper": paper,
                    "source_centre": f"Examination Centre {chr(65 + paper_index)}",
                    "expected_packets": max(1, expected // 40),
                    "expected_scripts": expected,
                    "received_packets": max(1, received // 40),
                    "received_scripts": received,
                    "status": Dispatch.Status.RECONCILED if expected == received else Dispatch.Status.EXCEPTION,
                    "received_at": timezone.now() - timedelta(hours=paper_index * 3 + 2),
                },
            )
            packet, _ = Packet.objects.get_or_create(tenant_id=tenant_id, dispatch=dispatch, barcode=f"PKT-{paper.code}-001", defaults={"expected_scripts": expected, "received_scripts": received, "status": "received"})
            for local_index in range(12):
                state = [Script.State.STORED, Script.State.ASSIGNED, Script.State.EVALUATING, Script.State.SUBMITTED][(local_index + paper_index) % 4]
                script, made = Script.objects.get_or_create(
                    tenant_id=tenant_id,
                    script_code=f"AS-{script_number:06d}",
                    defaults={"primary_barcode": f"NB-{paper.code}-{local_index + 1:05d}", "packet": packet, "paper": paper, "page_count": 24 + (local_index % 5), "state": state},
                )
                if settings.SEED_DEMO_ASSETS and script.page_count != 3:
                    script.page_count = 3
                    script.save(update_fields=["page_count", "updated_at"])
                if made:
                    CustodyEvent.objects.create(tenant_id=tenant_id, script=script, from_state=Script.State.MASKED, to_state=state, location="Digital Repository A", actor_id=str(user.id))
                seeded_assets = ScriptAsset.objects.filter(
                    script=script,
                    kind__in=[ScriptAsset.Kind.MASTER, ScriptAsset.Kind.EVALUATION, ScriptAsset.Kind.THUMBNAIL],
                ).count()
                if settings.SEED_DEMO_ASSETS and seeded_assets < 9:
                    ScriptAsset.objects.filter(script=script, storage_key__startswith="evaluation/").delete()
                    for page in range(1, 4):
                        destinations = [
                            {"key": f"scripts-master/{tenant_id}/{script.id}/page-{page}.webp"},
                            {"key": f"scripts-evaluation/{tenant_id}/{script.id}/page-{page}-v1.webp"},
                            {"key": f"scripts-thumbnails/{tenant_id}/{script.id}/page-{page}-v1.webp", "thumbnail": True},
                        ]
                        try:
                            generated = create_demo_page(paper=f"{paper.code} · {paper.title}", page_number=page, destinations=destinations)
                        except Exception:
                            generated = {"objects": []}
                        for item in generated["objects"]:
                            prefix = item["key"].split("/", 1)[0]
                            kind = {"scripts-master": ScriptAsset.Kind.MASTER, "scripts-evaluation": ScriptAsset.Kind.EVALUATION, "scripts-thumbnails": ScriptAsset.Kind.THUMBNAIL}[prefix]
                            ScriptAsset.objects.update_or_create(storage_key=item["key"], defaults={"tenant_id": tenant_id, "script": script, "kind": kind, "page_number": page, "sha256": item["sha256"], "byte_size": item["byte_size"], "mime_type": item["mime_type"], "version": 1, "retention_until": date(2033, 12, 31), "object_lock_until": date(2033, 12, 31) if kind == ScriptAsset.Kind.MASTER else None, "backup_status": "completed", "replication_status": "completed"})
                if state in [Script.State.ASSIGNED, Script.State.EVALUATING, Script.State.SUBMITTED]:
                    Assignment.objects.get_or_create(
                        tenant_id=tenant_id,
                        script=script,
                        valuation_round=1,
                        defaults={"evaluator": evaluators[(local_index + paper_index) % 4], "status": Assignment.Status.SUBMITTED if state == Script.State.SUBMITTED else Assignment.Status.IN_PROGRESS if state == Script.State.EVALUATING else Assignment.Status.ASSIGNED, "due_at": timezone.now() + timedelta(days=3 + local_index % 4), "quality_score": Decimal("92.50")},
                    )
                script_number += 1
            if paper_index >= 2:
                ReceivingException.objects.get_or_create(tenant_id=tenant_id, dispatch=dispatch, kind=ReceivingException.Kind.DISCREPANCY, defaults={"notes": "Physical count differs from dispatch manifest."})
        intake_packet = Packet.objects.filter(tenant_id=tenant_id, dispatch__paper=papers[0]).first()
        raw_manifests = []
        for index, state in enumerate((Script.State.REGISTERED, Script.State.SCANNED), 1):
            scan_script, _ = Script.objects.get_or_create(
                tenant_id=tenant_id,
                script_code=f"AS-SCAN-DEMO-{index}",
                defaults={"primary_barcode": f"NB-SCAN-DEMO-{index}", "packet": intake_packet, "paper": papers[0], "page_count": 3, "state": state, "last_location": "Digitization Hall A"},
            )
            manifest = []
            if settings.SEED_DEMO_ASSETS:
                for page in range(1, 4):
                    key = f"raw-scans/{tenant_id}/{scan_script.id}/page-{page}.webp"
                    try:
                        generated = create_demo_page(paper=f"{papers[0].code} source scan", page_number=page, destinations=[{"key": key}])
                        item = generated["objects"][0]
                        manifest.append({"storage_key": item["key"], "sha256": item["sha256"], "page_number": page, "barcode": scan_script.primary_barcode if page == 1 else ""})
                    except Exception:
                        manifest = []
                        break
            raw_manifests.append((scan_script, manifest))
        if not Script.objects.filter(tenant_id=tenant_id, state=Script.State.REGISTERED).exists():
            upload_reference = uuid4().hex[:12].upper()
            upload_script = Script.objects.create(
                tenant_id=tenant_id,
                script_code=f"AS-UPLOAD-{upload_reference}",
                primary_barcode=f"NB-UPLOAD-{upload_reference}",
                packet=intake_packet,
                paper=papers[0],
                page_count=0,
                state=Script.State.REGISTERED,
                last_location="Manual intake queue",
            )
            CustodyEvent.objects.create(
                tenant_id=tenant_id,
                script=upload_script,
                from_state="",
                to_state=Script.State.REGISTERED,
                location="Manual intake queue",
                actor_id=str(user.id),
                metadata={"source": "demo_replenishment"},
            )
        scanner = ScannerDevice.objects.get(tenant_id=tenant_id, code="SCAN-CENTRAL-01")
        queued_batch, _ = ScanBatch.objects.get_or_create(tenant_id=tenant_id, reference="SCAN-DEMO-QUEUED", defaults={"paper": papers[0], "requested_topology": ScannerDevice.Topology.CENTRAL, "duplex": True, "adf": True, "priority": 2, "expected_scripts": 1})
        ScanJob.objects.get_or_create(tenant_id=tenant_id, batch=queued_batch, script=raw_manifests[0][0], defaults={"expected_pages": 3})
        complete_batch, _ = ScanBatch.objects.get_or_create(tenant_id=tenant_id, reference="SCAN-DEMO-COMPLETE", defaults={"paper": papers[0], "scanner": scanner, "requested_topology": ScannerDevice.Topology.CENTRAL, "duplex": True, "adf": True, "priority": 3, "status": ScanBatch.Status.COMPLETED, "operator_id": user.id, "expected_scripts": 1, "completed_scripts": 1, "assigned_at": timezone.now() - timedelta(hours=2), "started_at": timezone.now() - timedelta(hours=2), "completed_at": timezone.now() - timedelta(hours=1)})
        complete_job, _ = ScanJob.objects.update_or_create(tenant_id=tenant_id, batch=complete_batch, script=raw_manifests[1][0], defaults={"status": ScanJob.Status.COMPLETED, "expected_pages": 3, "scanned_pages": len(raw_manifests[1][1]), "source_manifest": raw_manifests[1][1], "completed_at": timezone.now() - timedelta(hours=1)})
        if raw_manifests[1][1]:
            ProcessingRun.objects.get_or_create(tenant_id=tenant_id, script=raw_manifests[1][0], scan_job=complete_job, profile=profile, defaults={"source_digest": "seeded-source-manifest", "status": ProcessingRun.Status.QUEUED})
        for paper in papers:
            ModerationPolicy.objects.update_or_create(
                tenant_id=tenant_id,
                paper=paper,
                defaults={"sample_percentage": Decimal("10"), "sampling_modes": ["percentage_random", "failed_script", "high_score"], "high_score_threshold": Decimal("85"), "mandatory": paper.moderation_required},
            )
        centre, _ = CentreProfile.objects.update_or_create(
            tenant_id=tenant_id,
            code="NBU-CENTRAL",
            defaults={"name": "Northbridge Central Evaluation Centre", "location": "Academic Block C", "capacity": 120, "schedule": {"shift": "09:00-17:30"}, "security_controls": ["restricted-entry", "secure-lan", "cctv"], "supervisor_id": operators["controller@admiezo.local"].id, "scanner_ids": ["SCAN-CENTRAL-01"], "workstation_count": 96, "status": CentreProfile.Status.ACTIVE},
        )
        CentreReadiness.objects.get_or_create(
            tenant_id=tenant_id,
            centre=centre,
            decision="go",
            defaults={"scanner_ready": True, "workstation_ready": True, "network_ready": True, "power_ready": True, "secure_lan_ready": True, "operators_ready": True, "seat_plan": {"available": 96, "reserved": 8}, "notes": "Daily opening controls verified.", "checked_by_id": operators["controller@admiezo.local"].id},
        )
        EvaluationCamp.objects.update_or_create(
            tenant_id=tenant_id,
            centre=centre,
            session=session,
            name="November 2026 Central Evaluation Camp",
            defaults={"starts_at": session.evaluation_starts_at, "ends_at": session.evaluation_ends_at, "evaluator_ids": [str(item.id) for item in evaluators if item.status == Evaluator.Status.ACTIVE], "performance": {"target_scripts": 1048, "completed_scripts": 312}, "status": EvaluationCamp.Status.ACTIVE},
        )
        RemunerationRule.objects.get_or_create(
            tenant_id=tenant_id,
            paper=None,
            centre=centre,
            defaults={"per_script": Decimal("28"), "per_page": Decimal("0.50"), "per_question": Decimal("1.25"), "moderator_rate": Decimal("45"), "revaluation_rate": Decimal("90"), "slabs": [{"minimum": 100, "bonus": 500}], "minimum_payment": Decimal("500"), "maximum_payment": Decimal("25000"), "tax_percentage": Decimal("10")},
        )
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
        RuntimeIncident.objects.get_or_create(tenant_id=tenant_id, service="storage-gateway", category="transient_latency", status=RuntimeIncident.Status.RECOVERED, defaults={"severity": "medium", "last_confirmed_state": {"assets_available": True}, "recovery_point": {"retry": 2}, "retry_count": 2, "details": {"summary": "Automatic retry restored normal service."}, "detected_at": timezone.now() - timedelta(hours=6), "recovered_at": timezone.now() - timedelta(hours=5, minutes=54)})
        OperationalIssue.objects.get_or_create(tenant_id=tenant_id, title="scanner queue delayed", defaults={"issue_type": "scanner", "description": "One distributed scanner queue exceeded its normal processing interval.", "classification": "scanner", "priority": 3, "status": OperationalIssue.Status.ASSIGNED, "owner_id": operators["controller@admiezo.local"].id, "sla_due_at": timezone.now() + timedelta(hours=18), "created_by_id": user.id})
        NotificationDelivery.objects.get_or_create(tenant_id=tenant_id, user_id=user.id, title="Daily readiness completed", defaults={"category": "operations", "body": "Central evaluation centre readiness controls passed.", "severity": "normal", "channels": ["in_app"], "status": NotificationDelivery.Status.DELIVERED})
        self.stdout.write(self.style.SUCCESS(f"ADMIEZO workspace ready for {email}"))
