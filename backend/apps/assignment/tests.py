import hashlib
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from ninja.errors import HttpError

from apps.allocation.models import Assignment
from apps.identity_auth.models import AccessSession

from .services import acquire_lock


class AssignmentLockTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("bootstrap_demo", verbosity=0)

    def test_same_access_session_can_resume_but_another_session_is_blocked(self):
        assignment = Assignment.objects.exclude(status=Assignment.Status.SUBMITTED).select_related("evaluator").first()
        evaluator = assignment.evaluator
        user = User.objects.get(username=evaluator.email)
        first_session = AccessSession.objects.create(
            user=user,
            tenant_id=assignment.tenant_id,
            session_key_hash=hashlib.sha256(b"assignment-lock-session-one").hexdigest(),
            expires_at=timezone.now() + timedelta(hours=1),
        )
        second_session = AccessSession.objects.create(
            user=user,
            tenant_id=assignment.tenant_id,
            session_key_hash=hashlib.sha256(b"assignment-lock-session-two").hexdigest(),
            expires_at=timezone.now() + timedelta(hours=1),
        )

        first_lock, first_token = acquire_lock(
            tenant_id=assignment.tenant_id,
            actor_id=user.id,
            assignment=assignment,
            evaluator=evaluator,
            expected_version=assignment.version,
            access_session=first_session,
        )
        resumed_lock, resumed_token = acquire_lock(
            tenant_id=assignment.tenant_id,
            actor_id=user.id,
            assignment=assignment,
            evaluator=evaluator,
            expected_version=assignment.version,
            access_session=first_session,
        )

        self.assertEqual(resumed_lock.id, first_lock.id)
        self.assertNotEqual(resumed_token, first_token)
        with self.assertRaises(HttpError):
            acquire_lock(
                tenant_id=assignment.tenant_id,
                actor_id=user.id,
                assignment=assignment,
                evaluator=evaluator,
                expected_version=assignment.version,
                access_session=second_session,
            )
