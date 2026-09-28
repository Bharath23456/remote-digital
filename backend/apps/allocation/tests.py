import hashlib
from datetime import timedelta
import json
from urllib.parse import parse_qs, urlparse

from django.contrib.auth.models import User
from django.core.management import call_command
from apps.core.testing import create_operational_fixtures
from django.test import Client, TestCase
from django.utils import timezone
from ninja.errors import HttpError

from apps.configuration.models import Paper, Subject
from apps.core.models import AuditEvent, OutboxEvent
from apps.core.services import record_event
from apps.custody.models import Script
from apps.eligibility.models import EligibilityRecord
from apps.evaluators.models import Evaluator, Expertise
from apps.receiving.models import Dispatch, Packet
from apps.repository.models import ScriptAsset
from apps.phase4.models import NotificationDelivery
from apps.tenancy.models import Membership

from .models import AllocationPolicy, AllocationProposal, AllocationRun, Assignment, AssignmentHistory
from .services import build_plan, create_assignment, execute_plan, redistribute_assignment


class AllocationEngineTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("bootstrap_demo", verbosity=0)
        create_operational_fixtures()
        cls.actor = User.objects.get(username="admin@admiezo.local")

    def setUp(self):
        original = Paper.objects.first()
        case_id = hashlib.sha256(self._testMethodName.encode()).hexdigest()[:12]
        self.paper = Paper.objects.create(tenant_id=original.tenant_id, session=original.session, subject=original.subject, code=f"ALLOC-{case_id}", title="Allocation engine test", max_marks=100, pass_marks=40, valuation_rounds=2, status=Paper.Status.FROZEN)
        dispatch = Dispatch.objects.create(tenant_id=self.paper.tenant_id, reference=f"DSP-{case_id}", paper=self.paper, source_centre="Independent Test Centre", expected_packets=1, expected_scripts=4)
        packet = Packet.objects.create(tenant_id=self.paper.tenant_id, dispatch=dispatch, barcode=f"PKT-{case_id}", expected_scripts=4)
        self.scripts = [Script.objects.create(tenant_id=self.paper.tenant_id, script_code=f"AS-{case_id}-{index}", primary_barcode=f"BC-{case_id}-{index}", packet=packet, paper=self.paper, state=Script.State.STORED, page_count=3) for index in range(4)]
        self.evaluators = list(Evaluator.objects.filter(tenant_id=self.paper.tenant_id, status=Evaluator.Status.ACTIVE)[:3])
        for evaluator in self.evaluators:
            Expertise.objects.update_or_create(tenant_id=self.paper.tenant_id, evaluator=evaluator, subject=self.paper.subject, defaults={"level": 4, "verified": True, "years_experience": evaluator.years_experience})
            EligibilityRecord.objects.update_or_create(tenant_id=self.paper.tenant_id, evaluator=evaluator, subject=self.paper.subject, defaults={"status": EligibilityRecord.Status.ELIGIBLE, "qualification_ok": True, "experience_ok": True, "institution_ok": True, "expertise_ok": True, "has_conflict": False, "is_debarred": False, "is_blacklisted": False, "expires_on": timezone.localdate() + timedelta(days=90)})

    def test_intelligent_simulation_and_execution_are_blind_and_balanced(self):
        active_statuses = [Assignment.Status.ASSIGNED, Assignment.Status.ACCEPTED, Assignment.Status.IN_PROGRESS, Assignment.Status.REASSIGNED]
        baseline_loads = [Assignment.objects.filter(tenant_id=self.paper.tenant_id, evaluator=evaluator, status__in=active_statuses).count() for evaluator in self.evaluators]
        run = build_plan(tenant_id=self.paper.tenant_id, actor_id=self.actor.id, paper=self.paper, algorithm=AllocationPolicy.Algorithm.INTELLIGENT, valuation_round=1, maximum_scripts=4, mode=AllocationRun.Mode.SIMULATION)
        self.assertEqual(run.planned_scripts, 4)
        self.assertEqual(run.unallocated_scripts, 0)
        proposals = list(AllocationProposal.objects.filter(run=run))
        self.assertTrue(all(item.evaluator_id and item.quality_score > 0 for item in proposals))
        projected_loads = [baseline + sum(item.evaluator_id == evaluator.id for item in proposals) for baseline, evaluator in zip(baseline_loads, self.evaluators, strict=True)]
        baseline_spread = max(baseline_loads) - min(baseline_loads)
        self.assertLessEqual(max(projected_loads) - min(projected_loads), max(1, baseline_spread), projected_loads)
        self.assertNotIn("identity", " ".join(field.name for field in AllocationProposal._meta.fields))
        executed = execute_plan(tenant_id=self.paper.tenant_id, actor_id=self.actor.id, run_id=run.id)
        self.assertEqual(executed.status, AllocationRun.Status.COMPLETED)
        self.assertEqual(Assignment.objects.filter(script__paper=self.paper).count(), 4)
        self.assertTrue(all(state == Script.State.ASSIGNED for state in Script.objects.filter(paper=self.paper).values_list("state", flat=True)))
        self.assertTrue(AuditEvent.objects.filter(action="allocation.plan.executed", aggregate_id=str(run.id)).exists())
        self.assertTrue(OutboxEvent.objects.filter(topic="allocation.plan.executed", aggregate_id=str(run.id)).exists())

    def test_conflict_is_a_hard_block_for_manual_assignment(self):
        record = EligibilityRecord.objects.get(evaluator=self.evaluators[0], subject=self.paper.subject)
        record.has_conflict = True
        record.save(update_fields=["has_conflict"])
        with self.assertRaises(HttpError):
            create_assignment(tenant_id=self.paper.tenant_id, actor_id=self.actor.id, script=self.scripts[0], evaluator=self.evaluators[0], backup_evaluator=None, valuation_round=1, due_at=timezone.now() + timedelta(days=3), source="manual", quality_score=None, score_breakdown=None)
        self.assertFalse(Assignment.objects.filter(script=self.scripts[0]).exists())

    def test_manual_assignment_endpoint_is_disabled(self):
        client = Client()
        login = client.post(
            "/api/v1/auth/login",
            data=json.dumps({"email": "admin@admiezo.local", "password": "ChangeMe123!", "device_id": "allocation-round-test"}),
            content_type="application/json",
        )
        self.assertEqual(login.status_code, 200)
        payload = {
            "script_id": str(self.scripts[0].id),
            "evaluator_id": str(self.evaluators[0].id),
            "valuation_round": 2,
            "due_in_hours": 120,
            "priority": 3,
        }
        response = client.post("/api/v1/allocation/assignments", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(response.status_code, 410)
        self.assertIn("Manual allocation is disabled", response.json()["detail"])
        self.assertFalse(Assignment.objects.filter(script=self.scripts[0]).exists())

    def test_simulation_never_matches_an_unrelated_subject(self):
        civil = self.evaluators[0]
        other_subject = Subject.objects.filter(tenant_id=self.paper.tenant_id).exclude(id=self.paper.subject_id).first()
        self.assertIsNotNone(other_subject)
        Expertise.objects.filter(evaluator=civil, subject=self.paper.subject).delete()
        EligibilityRecord.objects.filter(evaluator=civil, subject=self.paper.subject).delete()
        Expertise.objects.update_or_create(tenant_id=self.paper.tenant_id, evaluator=civil, subject=other_subject, defaults={"level": 5, "verified": True, "years_experience": civil.years_experience})
        EligibilityRecord.objects.update_or_create(tenant_id=self.paper.tenant_id, evaluator=civil, subject=other_subject, defaults={"status": EligibilityRecord.Status.ELIGIBLE, "qualification_ok": True, "experience_ok": True, "institution_ok": True, "expertise_ok": True, "has_conflict": False, "is_debarred": False, "is_blacklisted": False, "expires_on": timezone.localdate() + timedelta(days=90)})
        run = build_plan(tenant_id=self.paper.tenant_id, actor_id=self.actor.id, paper=self.paper, algorithm=AllocationPolicy.Algorithm.INTELLIGENT, valuation_round=1, maximum_scripts=4, mode=AllocationRun.Mode.SIMULATION)
        self.assertFalse(run.proposals.filter(evaluator=civil).exists())

    def test_history_shows_simulation_actor_subject_and_matches(self):
        run = build_plan(tenant_id=self.paper.tenant_id, actor_id=self.actor.id, paper=self.paper, algorithm=AllocationPolicy.Algorithm.INTELLIGENT, valuation_round=1, maximum_scripts=4, mode=AllocationRun.Mode.SIMULATION)
        execute_plan(tenant_id=self.paper.tenant_id, actor_id=self.actor.id, run_id=run.id)
        client = Client()
        login = client.post("/api/v1/auth/login", data=json.dumps({"email": "admin@admiezo.local", "password": "ChangeMe123!", "device_id": "allocation-history-test"}), content_type="application/json")
        self.assertEqual(login.status_code, 200)
        catalog = client.get("/api/v1/allocation/catalog")
        paper = next(item for item in catalog.json()["papers"] if item["id"] == str(self.paper.id))
        self.assertEqual(paper["subject_code"], self.paper.subject.code)
        response = client.get("/api/v1/allocation/history")
        self.assertEqual(response.status_code, 200)
        rows = [row for row in response.json()["rows"] if row["run_id"] == str(run.id)]
        self.assertEqual(len(rows), 4)
        self.assertEqual({row["subject"] for row in rows}, {self.paper.subject.code})
        self.assertEqual({row["ran_by"] for row in rows}, {self.actor.get_full_name() or self.actor.username})
        self.assertTrue(all(row["evaluator"] and row["status"] == "completed" for row in rows))
        searched = client.get(f"/api/v1/allocation/history?q={self.scripts[0].script_code}")
        self.assertEqual(searched.status_code, 200)
        self.assertEqual(searched.json()["total"], 1)
        self.assertEqual(searched.json()["rows"][0]["script"], self.scripts[0].script_code)
        self.assertEqual(client.get("/api/v1/allocation/history?page=0").status_code, 422)

    def test_redistribution_uses_backup_and_preserves_history(self):
        assignment = create_assignment(tenant_id=self.paper.tenant_id, actor_id=self.actor.id, script=self.scripts[0], evaluator=self.evaluators[0], backup_evaluator=self.evaluators[1], valuation_round=1, due_at=timezone.now() + timedelta(days=3), source="manual", quality_score=None, score_breakdown=None)
        updated = redistribute_assignment(tenant_id=self.paper.tenant_id, actor_id=self.actor.id, assignment_id=assignment.id, expected_version=assignment.version, reason="Primary evaluator became unavailable")
        self.assertEqual(updated.evaluator_id, self.evaluators[1].id)
        self.assertEqual(updated.status, Assignment.Status.REASSIGNED)
        self.assertEqual(AssignmentHistory.objects.filter(assignment=assignment).count(), 2)

    def test_assignment_notifies_the_linked_evaluator(self):
        evaluator = self.evaluators[0]
        self.assertIsNotNone(evaluator.user_id)
        assignment = create_assignment(tenant_id=self.paper.tenant_id, actor_id=self.actor.id, script=self.scripts[0], evaluator=evaluator, backup_evaluator=None, valuation_round=1, due_at=timezone.now() + timedelta(days=3), source="manual", quality_score=None, score_breakdown=None)
        notification = NotificationDelivery.objects.get(tenant_id=self.paper.tenant_id, user_id=evaluator.user_id, category="assignment")
        self.assertEqual(notification.status, NotificationDelivery.Status.DELIVERED)
        self.assertIn(assignment.script.script_code, notification.body)

    def test_secure_viewer_manifest_uses_five_minute_signed_urls_and_tracks_progress(self):
        assignment = create_assignment(tenant_id=self.paper.tenant_id, actor_id=self.actor.id, script=self.scripts[0], evaluator=self.evaluators[0], backup_evaluator=None, valuation_round=1, due_at=timezone.now() + timedelta(days=3), source="manual", quality_score=None, score_breakdown=None)
        ScriptAsset.objects.create(tenant_id=self.paper.tenant_id, script=self.scripts[0], kind=ScriptAsset.Kind.EVALUATION, page_number=1, storage_key=f"scripts-evaluation/{self.scripts[0].id}/page-1.webp", sha256="b" * 64, byte_size=2048, mime_type="image/webp")
        client = Client()
        response = client.post("/api/v1/auth/login", data=json.dumps({"email": "admin@admiezo.local", "password": "ChangeMe123!", "device_id": "viewer-test"}), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        started = client.post(f"/api/v1/allocation/assignments/{assignment.id}/action", data=json.dumps({"version": assignment.version, "action": "start"}), content_type="application/json")
        self.assertEqual(started.status_code, 200)
        manifest = client.get(f"/api/v1/allocation/assignments/{assignment.id}/viewer")
        self.assertEqual(manifest.status_code, 200)
        self.assertEqual(manifest.json()["ttl_seconds"], 300)
        expires = int(parse_qs(urlparse(manifest.json()["pages"][0]["url"]).query)["expires"][0])
        self.assertLessEqual(expires - int(timezone.now().timestamp()), 300)
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, Assignment.Status.IN_PROGRESS)
        self.scripts[0].refresh_from_db()
        self.assertEqual(self.scripts[0].state, Script.State.EVALUATING)

    def test_evaluator_catalog_contains_only_their_queue(self):
        evaluator = Evaluator.objects.get(email="evaluator1043@admiezo.local")
        membership = Membership.objects.get(user=evaluator.user, institution__tenant_id=evaluator.tenant_id)
        membership.enabled_modules = ["evaluation"]
        membership.save(update_fields=["enabled_modules"])
        client = Client()
        login = client.post(
            "/api/v1/auth/login",
            data=json.dumps({"email": evaluator.email, "password": "ChangeMe123!", "device_id": "rbac-test"}),
            content_type="application/json",
        )
        self.assertEqual(login.status_code, 200)
        response = client.get("/api/v1/allocation/catalog")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.get("/api/v1/evaluator-management/face/status").status_code, 200)
        self.assertEqual(client.get("/api/v1/phase4/remote-security/policy").status_code, 200)
        self.assertEqual(client.get("/api/v1/allocation/history").status_code, 403)
        body = response.json()
        self.assertTrue(body["assignments"])
        self.assertTrue(all(item["id"] in {str(value) for value in Assignment.objects.filter(evaluator=evaluator).values_list("id", flat=True)} for item in body["assignments"]))
        self.assertEqual(body["scripts"], [])
        self.assertEqual(body["papers"], [])
        self.assertEqual(body["evaluators"], [])
        self.assertEqual(body["policies"], [])
        self.assertEqual(body["runs"], [])
        self.assertNotIn("evaluator_id", body["assignments"][0])

    def test_evaluator_self_allocates_or_notifies_engine_after_completing_queue(self):
        user = User.objects.create_user(
            username=f"allocate-more-{self._testMethodName}@example.test",
            email=f"allocate-more-{self._testMethodName}@example.test",
            password="ChangeMe123!",
            first_name="Queue",
            last_name="Evaluator",
        )
        institution = Membership.objects.filter(institution__tenant_id=self.paper.tenant_id).first().institution
        Membership.objects.create(
            user=user,
            institution=institution,
            role=Membership.Role.EVALUATOR,
            enabled_modules=["evaluation"],
        )
        evaluator = Evaluator.objects.create(
            tenant_id=self.paper.tenant_id,
            user=user,
            evaluator_code=f"MORE-{self.paper.code[-8:]}",
            display_name="Queue Evaluator",
            email=user.email,
            institution_name="Independent University",
            department="Evaluation",
            designation="Evaluator",
            qualification="Masters",
            years_experience=8,
            daily_capacity=2,
            status=Evaluator.Status.ACTIVE,
        )
        Expertise.objects.create(
            tenant_id=self.paper.tenant_id,
            evaluator=evaluator,
            subject=self.paper.subject,
            level=5,
            years_experience=8,
            verified=True,
        )
        EligibilityRecord.objects.create(
            tenant_id=self.paper.tenant_id,
            evaluator=evaluator,
            subject=self.paper.subject,
            status=EligibilityRecord.Status.ELIGIBLE,
            qualification_ok=True,
            experience_ok=True,
            institution_ok=True,
            expertise_ok=True,
            has_conflict=False,
            is_debarred=False,
            is_blacklisted=False,
            expires_on=timezone.localdate() + timedelta(days=90),
        )
        completed = create_assignment(
            tenant_id=self.paper.tenant_id,
            actor_id=self.actor.id,
            script=self.scripts[0],
            evaluator=evaluator,
            backup_evaluator=self.evaluators[0],
            valuation_round=1,
            due_at=timezone.now() + timedelta(days=3),
            source="intelligent",
            quality_score=None,
            score_breakdown=None,
        )
        Assignment.objects.filter(id=completed.id).update(
            status=Assignment.Status.SUBMITTED,
            progress_percent=100,
            submitted_at=timezone.now(),
        )
        record_event(
            tenant_id=self.paper.tenant_id,
            actor_id=user.id,
            action="allocation.evaluator.more_requested",
            aggregate="Evaluator",
            aggregate_id=evaluator.id,
            payload={"allocated": 2, "available_capacity": 2},
        )

        client = Client()
        self.assertEqual(client.post(
            "/api/v1/auth/login",
            data=json.dumps({"email": user.email, "password": "ChangeMe123!", "device_id": "allocate-more-test"}),
            content_type="application/json",
        ).status_code, 200)
        catalog = client.get("/api/v1/allocation/catalog")
        self.assertEqual(catalog.status_code, 200)
        self.assertTrue(catalog.json()["allocation_more"]["eligible"])
        self.assertFalse(catalog.json()["allocation_more"]["pending"])

        assignment_count = Assignment.objects.filter(tenant_id=self.paper.tenant_id, evaluator=evaluator).count()
        response = client.post("/api/v1/allocation/allocate-more")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "allocated")
        self.assertEqual(response.json()["allocated"], 2)
        allocated_ids = {item["id"] for item in response.json()["assignments"]}
        self.assertEqual(Assignment.objects.filter(id__in=allocated_ids, evaluator=evaluator, source="evaluator_request").count(), 2)
        self.assertEqual(Assignment.objects.filter(tenant_id=self.paper.tenant_id, evaluator=evaluator).count(), assignment_count + 2)
        self.assertEqual(NotificationDelivery.objects.filter(tenant_id=self.paper.tenant_id, category="allocation_request").count(), 0)

        Assignment.objects.filter(id__in=allocated_ids).update(
            status=Assignment.Status.SUBMITTED,
            progress_percent=100,
            submitted_at=timezone.now(),
        )
        allocated_script_ids = Assignment.objects.filter(id__in=allocated_ids).values_list("script_id", flat=True)
        Script.objects.filter(tenant_id=self.paper.tenant_id).exclude(id__in=allocated_script_ids).exclude(id=completed.script_id).update(state=Script.State.ARCHIVED)

        fallback_count = Assignment.objects.filter(tenant_id=self.paper.tenant_id, evaluator=evaluator).count()
        response = client.post("/api/v1/allocation/allocate-more")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "requested")
        self.assertGreater(response.json()["recipient_count"], 0)
        self.assertEqual(Assignment.objects.filter(tenant_id=self.paper.tenant_id, evaluator=evaluator).count(), fallback_count)
        self.assertTrue(AuditEvent.objects.filter(action="allocation.evaluator.more_requested", aggregate_id=str(evaluator.id)).exists())
        notification = NotificationDelivery.objects.filter(
            tenant_id=self.paper.tenant_id,
            user_id=self.actor.id,
            category="allocation_request",
        ).latest("created_at")
        self.assertEqual(notification.status, NotificationDelivery.Status.DELIVERED)
        self.assertIn(evaluator.display_name, notification.title)

        admin_client = Client()
        self.assertEqual(admin_client.post(
            "/api/v1/auth/login",
            data=json.dumps({"email": "admin@admiezo.local", "password": "ChangeMe123!", "device_id": "allocation-request-admin"}),
            content_type="application/json",
        ).status_code, 200)
        admin_catalog = admin_client.get("/api/v1/allocation/catalog")
        self.assertEqual(admin_catalog.status_code, 200)
        self.assertIn(str(notification.id), {item["id"] for item in admin_catalog.json()["allocation_requests"]})

        notification_count = NotificationDelivery.objects.filter(tenant_id=self.paper.tenant_id, category="allocation_request").count()
        retry = client.post("/api/v1/allocation/allocate-more")
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json()["status"], "requested")
        self.assertEqual(retry.json()["recipient_count"], 0)
        self.assertIn("remains pending", retry.json()["message"])
        self.assertEqual(NotificationDelivery.objects.filter(tenant_id=self.paper.tenant_id, category="allocation_request").count(), notification_count)
        pending_catalog = client.get("/api/v1/allocation/catalog").json()["allocation_more"]
        self.assertTrue(pending_catalog["eligible"])
        self.assertTrue(pending_catalog["pending"])

        self.scripts[3].state = Script.State.STORED
        self.scripts[3].save(update_fields=["state", "updated_at"])
        available_retry = client.post("/api/v1/allocation/allocate-more")
        self.assertEqual(available_retry.status_code, 200)
        self.assertEqual(available_retry.json()["status"], "allocated")
        self.assertEqual(available_retry.json()["allocated"], 1)
        notification.refresh_from_db()
        self.assertEqual(notification.status, NotificationDelivery.Status.ACKNOWLEDGED)
        self.assertNotIn(str(notification.id), {item["id"] for item in admin_client.get("/api/v1/allocation/catalog").json()["allocation_requests"]})

        AuditEvent.objects.filter(action="allocation.evaluator.more_requested", aggregate_id=str(evaluator.id)).delete()
        Assignment.objects.filter(id=completed.id).update(is_flagged=True)
        flagged_catalog = client.get("/api/v1/allocation/catalog")
        self.assertFalse(flagged_catalog.json()["allocation_more"]["eligible"])
        self.assertIn("flagged", flagged_catalog.json()["allocation_more"]["reason"].lower())
        self.assertEqual(client.post("/api/v1/allocation/allocate-more").status_code, 409)
