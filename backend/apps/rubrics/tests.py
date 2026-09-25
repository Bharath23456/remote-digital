from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from ninja.errors import HttpError

from apps.configuration.models import Paper
from apps.core.models import AuditEvent
from apps.tenancy.models import Membership

from .models import MarkingScheme, RubricCriterion
from .services import delete_criterion


class RubricCriterionDeletionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("bootstrap_demo", verbosity=0)
        cls.actor = User.objects.get(username="admin@admiezo.local")
        cls.paper = Paper.objects.prefetch_related("questions").first()
        cls.tenant_id = Membership.objects.filter(user=cls.actor).values_list(
            "institution__tenant_id", flat=True
        ).first()

    def create_criterion(self, *, status=MarkingScheme.Status.DRAFT):
        scheme = MarkingScheme.objects.create(
            tenant_id=self.tenant_id,
            paper=self.paper,
            version=99,
            title="Deletion test scheme",
            evaluation_guidelines="Test guidance",
            examiner_instructions="Test instructions",
            status=status,
            created_by_id=self.actor.id,
        )
        criterion = RubricCriterion.objects.create(
            tenant_id=self.tenant_id,
            scheme=scheme,
            question=self.paper.questions.first(),
            code="DELETE-ME",
            description="Temporary criterion",
            max_marks=1,
        )
        return scheme, criterion

    def test_draft_criterion_deletion_is_audited(self):
        scheme, criterion = self.create_criterion()

        delete_criterion(
            tenant_id=self.tenant_id,
            actor_id=self.actor.id,
            scheme_id=scheme.id,
            criterion_id=criterion.id,
        )

        self.assertFalse(RubricCriterion.objects.filter(id=criterion.id).exists())
        event = AuditEvent.objects.get(
            action="rubric.criterion.deleted", aggregate_id=str(criterion.id)
        )
        self.assertEqual(event.payload["scheme_id"], str(scheme.id))
        self.assertEqual(event.payload["code"], "DELETE-ME")

    def test_non_draft_criterion_cannot_be_deleted(self):
        scheme, criterion = self.create_criterion(status=MarkingScheme.Status.REVIEW)

        with self.assertRaisesMessage(HttpError, "only change while the scheme is in draft"):
            delete_criterion(
                tenant_id=self.tenant_id,
                actor_id=self.actor.id,
                scheme_id=scheme.id,
                criterion_id=criterion.id,
            )

        self.assertTrue(RubricCriterion.objects.filter(id=criterion.id).exists())
