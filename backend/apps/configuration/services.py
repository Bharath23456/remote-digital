from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from apps.configuration.models import (
    AcademicYear,
    ConfigurationApproval,
    ConfigurationChangeApproval,
    ConfigurationChangeRequest,
    ConfigurationRevision,
    Course,
    EvaluationCentre,
    EvaluationEvent,
    ExamSession,
    Paper,
    Programme,
    Question,
    Regulation,
    Subject,
    Term,
)
from apps.core.services import record_event
from apps.core.idempotency import begin_idempotent, complete_idempotent


class ConfigurationError(Exception):
    pass


class ConfigurationConflict(ConfigurationError):
    pass


def _row(item, fields):
    result = {"id": str(item.id)}
    for field in fields:
        value = getattr(item, field)
        if field.endswith("_id"):
            value = str(value)
        elif isinstance(value, Decimal):
            value = str(value)
        elif hasattr(value, "isoformat"):
            value = value.isoformat()
        result[field] = value
    return result


def configuration_catalog(tenant_id):
    return {
        "academic_years": [_row(item, ("label", "starts_on", "ends_on")) for item in AcademicYear.objects.filter(tenant_id=tenant_id).order_by("-starts_on")],
        "regulations": [_row(item, ("code", "title", "effective_from", "effective_to", "is_active")) for item in Regulation.objects.filter(tenant_id=tenant_id).order_by("code")],
        "terms": [_row(item, ("academic_year_id", "name", "sequence", "starts_on", "ends_on")) for item in Term.objects.filter(tenant_id=tenant_id).order_by("academic_year", "sequence")],
        "sessions": [_row(item, ("academic_year_id", "name", "term", "evaluation_starts_at", "evaluation_ends_at", "status", "version")) for item in ExamSession.objects.filter(tenant_id=tenant_id).order_by("-evaluation_starts_at")],
        "events": [_row(item, ("session_id", "name", "starts_at", "ends_at", "evaluation_centre_ids", "is_active")) for item in EvaluationEvent.objects.filter(tenant_id=tenant_id).order_by("starts_at")],
        "programmes": [_row(item, ("code", "name", "regulation")) for item in Programme.objects.filter(tenant_id=tenant_id).order_by("code")],
        "courses": [
            {
                **_row(item, ("programme_id", "regulation_id", "code", "name", "duration_terms", "is_active")),
                "programme": item.programme.code,
                "regulation": item.regulation.code,
            }
            for item in Course.objects.filter(tenant_id=tenant_id).select_related("programme", "regulation").order_by("code")
        ],
        "subjects": [
            {
                **_row(item, ("programme_id", "course_id", "code", "name", "semester", "session_ids", "related_subject_ids")),
                "programme": item.programme.code,
                "course": item.course.code if item.course else "—",
            }
            for item in Subject.objects.filter(tenant_id=tenant_id).select_related("programme", "course").order_by("code")
        ],
        "centres": [_row(item, ("code", "name", "address", "network_cidrs", "is_active")) for item in EvaluationCentre.objects.filter(tenant_id=tenant_id).order_by("code")],
        "papers": [paper_detail(item) for item in Paper.objects.filter(tenant_id=tenant_id).select_related("session", "subject").prefetch_related("questions", "approvals").order_by("code")],
        "change_requests": change_request_rows(tenant_id),
    }


def paper_snapshot(paper):
    return {
        "code": paper.code,
        "title": paper.title,
        "session_id": str(paper.session_id),
        "subject_id": str(paper.subject_id),
        "max_marks": str(paper.max_marks),
        "pass_marks": str(paper.pass_marks),
        "valuation_rounds": paper.valuation_rounds,
        "discrepancy_threshold": str(paper.discrepancy_threshold),
        "moderation_required": paper.moderation_required,
        "rules": paper.rules,
        "status": paper.status,
        "effective_from": paper.effective_from.isoformat() if paper.effective_from else None,
        "questions": [
            {"number": question.number, "max_marks": str(question.max_marks), "required": question.required, "position": question.position}
            for question in paper.questions.all()
        ],
    }


def paper_readiness(paper):
    issues = []
    questions = list(paper.questions.all())
    if not questions:
        issues.append("At least one question is required")
    if sum((item.max_marks for item in questions), Decimal("0")) != paper.max_marks:
        issues.append("Question marks must equal the paper maximum")
    if paper.pass_marks < 0 or paper.pass_marks > paper.max_marks:
        issues.append("Passing marks must be between zero and maximum marks")
    if paper.valuation_rounds not in {1, 2, 3}:
        issues.append("Valuation rounds must be one, two or three")
    if paper.discrepancy_threshold < 0 or paper.discrepancy_threshold > paper.max_marks:
        issues.append("Discrepancy threshold is outside the valid range")
    if paper.session.evaluation_starts_at >= paper.session.evaluation_ends_at:
        issues.append("Evaluation window must end after it starts")
    return {"ready": not issues, "issues": issues}


def paper_detail(paper):
    readiness = paper_readiness(paper)
    return {
        **_row(paper, ("session_id", "subject_id", "code", "title", "max_marks", "pass_marks", "valuation_rounds", "discrepancy_threshold", "moderation_required", "rules", "status", "version", "effective_from", "frozen_at")),
        "questions": [_row(item, ("number", "max_marks", "required", "position")) for item in paper.questions.all()],
        "approval_count": paper.approvals.filter(decision=ConfigurationApproval.Decision.APPROVED).count(),
        "readiness": readiness,
    }


def _related(model, tenant_id, item_id, label):
    item = model.objects.filter(id=item_id, tenant_id=tenant_id).first()
    if not item:
        raise ConfigurationError(f"{label} was not found in this university")
    return item


def _create_simple(*, model, tenant_id, actor_id, action, aggregate, values):
    try:
        with transaction.atomic():
            item = model.objects.create(tenant_id=tenant_id, **values)
            record_event(tenant_id=tenant_id, actor_id=actor_id, action=action, aggregate=aggregate, aggregate_id=item.id, payload={"id": str(item.id)})
            return item
    except IntegrityError as exc:
        raise ConfigurationConflict(f"{aggregate} already exists") from exc


def create_academic_year(*, tenant_id, actor_id, **values):
    if values["starts_on"] >= values["ends_on"]:
        raise ConfigurationError("Academic year end date must follow its start date")
    return _create_simple(model=AcademicYear, tenant_id=tenant_id, actor_id=actor_id, action="config.academic_year.created", aggregate="AcademicYear", values=values)


def create_regulation(*, tenant_id, actor_id, **values):
    if values.get("effective_to") and values["effective_from"] >= values["effective_to"]:
        raise ConfigurationError("Regulation end date must follow its effective date")
    return _create_simple(model=Regulation, tenant_id=tenant_id, actor_id=actor_id, action="config.regulation.created", aggregate="Regulation", values=values)


def create_term(*, tenant_id, actor_id, academic_year_id, **values):
    year = _related(AcademicYear, tenant_id, academic_year_id, "Academic year")
    if values["starts_on"] < year.starts_on or values["ends_on"] > year.ends_on or values["starts_on"] >= values["ends_on"]:
        raise ConfigurationError("Term dates must be ordered and contained in the academic year")
    return _create_simple(model=Term, tenant_id=tenant_id, actor_id=actor_id, action="config.term.created", aggregate="Term", values={"academic_year": year, **values})


def create_session(*, tenant_id, actor_id, academic_year_id, **values):
    year = _related(AcademicYear, tenant_id, academic_year_id, "Academic year")
    if values["evaluation_starts_at"] >= values["evaluation_ends_at"]:
        raise ConfigurationError("Evaluation end time must follow its start time")
    return _create_simple(model=ExamSession, tenant_id=tenant_id, actor_id=actor_id, action="config.session.created", aggregate="ExamSession", values={"academic_year": year, **values})


def create_event(*, tenant_id, actor_id, session_id, evaluation_centre_ids, **values):
    session = _related(ExamSession, tenant_id, session_id, "Examination session")
    if values["starts_at"] >= values["ends_at"]:
        raise ConfigurationError("Evaluation event end time must follow its start time")
    if values["starts_at"] < session.evaluation_starts_at or values["ends_at"] > session.evaluation_ends_at:
        raise ConfigurationError("Evaluation event must be inside the session evaluation window")
    centre_ids = [str(item) for item in evaluation_centre_ids]
    existing = set(EvaluationCentre.objects.filter(tenant_id=tenant_id, id__in=centre_ids, is_active=True).values_list("id", flat=True))
    if len(existing) != len(set(centre_ids)):
        raise ConfigurationError("Every evaluation centre must be active and belong to this university")
    return _create_simple(
        model=EvaluationEvent,
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="config.evaluation_event.created",
        aggregate="EvaluationEvent",
        values={"session": session, "evaluation_centre_ids": centre_ids, **values},
    )


def create_programme(*, tenant_id, actor_id, **values):
    return _create_simple(model=Programme, tenant_id=tenant_id, actor_id=actor_id, action="config.programme.created", aggregate="Programme", values=values)


def create_course(*, tenant_id, actor_id, programme_id, regulation_id, **values):
    programme = _related(Programme, tenant_id, programme_id, "Programme")
    regulation = _related(Regulation, tenant_id, regulation_id, "Regulation")
    return _create_simple(model=Course, tenant_id=tenant_id, actor_id=actor_id, action="config.course.created", aggregate="Course", values={"programme": programme, "regulation": regulation, **values})


def create_subject(*, tenant_id, actor_id, programme_id, course_id=None, session_ids=None, related_subject_ids=None, **values):
    programme = _related(Programme, tenant_id, programme_id, "Programme")
    course = _related(Course, tenant_id, course_id, "Course") if course_id else None
    if course and course.programme_id != programme.id:
        raise ConfigurationError("Course must belong to the selected programme")
    normalized_sessions = list(dict.fromkeys(str(item) for item in (session_ids or [])))
    normalized_related = list(dict.fromkeys(str(item) for item in (related_subject_ids or [])))
    if normalized_sessions and ExamSession.objects.filter(tenant_id=tenant_id, id__in=normalized_sessions).count() != len(normalized_sessions):
        raise ConfigurationError("Every selected session must belong to this university")
    if normalized_related and Subject.objects.filter(tenant_id=tenant_id, id__in=normalized_related).count() != len(normalized_related):
        raise ConfigurationError("Every related subject must belong to this university")
    return _create_simple(
        model=Subject,
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="config.subject.created",
        aggregate="Subject",
        values={
            "programme": programme,
            "course": course,
            "session_ids": normalized_sessions,
            "related_subject_ids": normalized_related,
            **values,
        },
    )


def create_centre(*, tenant_id, actor_id, **values):
    return _create_simple(model=EvaluationCentre, tenant_id=tenant_id, actor_id=actor_id, action="config.centre.created", aggregate="EvaluationCentre", values=values)


@transaction.atomic
def create_paper(*, tenant_id, actor_id, session_id, subject_id, **values):
    session = _related(ExamSession, tenant_id, session_id, "Examination session")
    subject = _related(Subject, tenant_id, subject_id, "Subject")
    if values["pass_marks"] > values["max_marks"]:
        raise ConfigurationError("Passing marks cannot exceed maximum marks")
    try:
        paper = Paper.objects.create(tenant_id=tenant_id, session=session, subject=subject, **values)
    except IntegrityError as exc:
        raise ConfigurationConflict("Paper code already exists in this examination session") from exc
    ConfigurationRevision.objects.create(
        tenant_id=tenant_id,
        aggregate_type="Paper",
        aggregate_id=paper.id,
        version=paper.version,
        change_type=ConfigurationRevision.ChangeType.CREATE,
        snapshot=paper_snapshot(paper),
        actor_id=actor_id,
    )
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="config.paper.created", aggregate="Paper", aggregate_id=paper.id, payload={"code": paper.code, "version": paper.version})
    return paper


@transaction.atomic
def add_question(*, tenant_id, actor_id, paper_id, values):
    paper = Paper.objects.select_for_update().filter(id=paper_id, tenant_id=tenant_id).first()
    if not paper:
        raise ConfigurationError("Paper was not found")
    if paper.status != Paper.Status.DRAFT:
        raise ConfigurationConflict("Questions can only be changed while a paper is in Draft. Create a governed revision for a paper already under review, approved, or frozen.")
    try:
        question = Question.objects.create(tenant_id=tenant_id, paper=paper, **values)
    except IntegrityError as exc:
        raise ConfigurationConflict("Question number already exists in this paper") from exc
    paper.version += 1
    paper.save(update_fields=["version", "updated_at"])
    ConfigurationRevision.objects.create(tenant_id=tenant_id, aggregate_type="Paper", aggregate_id=paper.id, version=paper.version, change_type=ConfigurationRevision.ChangeType.UPDATE, snapshot=paper_snapshot(paper), actor_id=actor_id, reason="Question added")
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="config.question.created", aggregate="Question", aggregate_id=question.id, payload={"paper_id": str(paper.id), "paper_version": paper.version})
    return question, paper


@transaction.atomic
def submit_paper(*, tenant_id, actor_id, paper_id, version, note, idempotency_key):
    record, replay_id = begin_idempotent(tenant_id=tenant_id, scope="configuration.paper.submit", key=idempotency_key, payload={"paper_id": paper_id, "version": version, "note": note})
    if replay_id:
        return Paper.objects.get(id=replay_id, tenant_id=tenant_id)
    paper = Paper.objects.select_for_update().prefetch_related("questions").filter(id=paper_id, tenant_id=tenant_id).first()
    if not paper:
        raise ConfigurationError("Paper was not found")
    if paper.version != version:
        raise ConfigurationConflict("Paper was changed by another user")
    if paper.status != Paper.Status.DRAFT:
        raise ConfigurationConflict("Only draft papers can be submitted for approval")
    readiness = paper_readiness(paper)
    if not readiness["ready"]:
        raise ConfigurationError("; ".join(readiness["issues"]))
    paper.status = Paper.Status.REVIEW
    paper.version += 1
    paper.save(update_fields=["status", "version", "updated_at"])
    ConfigurationRevision.objects.create(tenant_id=tenant_id, aggregate_type="Paper", aggregate_id=paper.id, version=paper.version, change_type=ConfigurationRevision.ChangeType.UPDATE, snapshot=paper_snapshot(paper), actor_id=actor_id, reason=note or "Submitted for approval")
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="config.paper.submitted", aggregate="Paper", aggregate_id=paper.id, payload={"version": paper.version})
    complete_idempotent(record, paper.id)
    return paper


@transaction.atomic
def approve_paper(*, tenant_id, actor_id, paper_id, version, note):
    paper = Paper.objects.select_for_update().prefetch_related("questions", "approvals").filter(id=paper_id, tenant_id=tenant_id).first()
    if not paper:
        raise ConfigurationError("Paper was not found")
    if paper.version != version:
        raise ConfigurationConflict("Paper was changed by another user")
    if paper.status != Paper.Status.REVIEW:
        raise ConfigurationConflict("Paper is not awaiting approval")
    if paper.approvals.filter(actor_id=actor_id, decision=ConfigurationApproval.Decision.APPROVED).exists():
        raise ConfigurationConflict("This administrator has already approved the paper")
    stage = paper.approvals.count() + 1
    ConfigurationApproval.objects.create(tenant_id=tenant_id, paper=paper, stage=stage, decision=ConfigurationApproval.Decision.APPROVED, actor_id=actor_id, note=note)
    required = 2 if paper.rules.get("critical_change", False) else 1
    if paper.approvals.filter(decision=ConfigurationApproval.Decision.APPROVED).count() >= required:
        paper.status = Paper.Status.APPROVED
        paper.approved_by_id = actor_id
    paper.version += 1
    paper.save(update_fields=["status", "approved_by_id", "version", "updated_at"])
    ConfigurationRevision.objects.create(tenant_id=tenant_id, aggregate_type="Paper", aggregate_id=paper.id, version=paper.version, change_type=ConfigurationRevision.ChangeType.APPROVE, snapshot=paper_snapshot(paper), actor_id=actor_id, reason=note)
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="config.paper.approved", aggregate="Paper", aggregate_id=paper.id, payload={"stage": stage, "required": required, "version": paper.version})
    return paper


@transaction.atomic
def freeze_paper(*, tenant_id, actor_id, paper_id, version, note):
    paper = Paper.objects.select_for_update().prefetch_related("questions").filter(id=paper_id, tenant_id=tenant_id).first()
    if not paper:
        raise ConfigurationError("Paper was not found")
    if paper.version != version:
        raise ConfigurationConflict("Paper was changed by another user")
    if paper.status != Paper.Status.APPROVED:
        raise ConfigurationConflict("Only approved papers can be frozen")
    paper.status = Paper.Status.FROZEN
    paper.frozen_at = timezone.now()
    paper.version += 1
    paper.save(update_fields=["status", "frozen_at", "version", "updated_at"])
    ConfigurationRevision.objects.create(tenant_id=tenant_id, aggregate_type="Paper", aggregate_id=paper.id, version=paper.version, change_type=ConfigurationRevision.ChangeType.FREEZE, snapshot=paper_snapshot(paper), actor_id=actor_id, reason=note)
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="config.paper.frozen", aggregate="Paper", aggregate_id=paper.id, payload={"version": paper.version})
    return paper


def paper_history(tenant_id, paper_id):
    return list(ConfigurationRevision.objects.filter(tenant_id=tenant_id, aggregate_type="Paper", aggregate_id=paper_id).values("version", "change_type", "snapshot", "reason", "actor_id", "effective_from", "created_at"))


PAPER_MUTABLE_FIELDS = {
    "title",
    "max_marks",
    "pass_marks",
    "valuation_rounds",
    "discrepancy_threshold",
    "moderation_required",
    "rules",
    "effective_from",
}


def _normalise_changes(changes):
    unknown = set(changes) - PAPER_MUTABLE_FIELDS
    if unknown:
        raise ConfigurationError(f"Unsupported paper fields: {', '.join(sorted(unknown))}")
    result = dict(changes)
    for field in ("max_marks", "pass_marks", "discrepancy_threshold"):
        if field in result:
            result[field] = str(Decimal(str(result[field])))
    if "effective_from" in result and result["effective_from"] is not None:
        value = result["effective_from"]
        result["effective_from"] = value.isoformat() if hasattr(value, "isoformat") else str(value)
    return result


def _validate_snapshot(snapshot):
    maximum = Decimal(str(snapshot["max_marks"]))
    passing = Decimal(str(snapshot["pass_marks"]))
    discrepancy = Decimal(str(snapshot["discrepancy_threshold"]))
    if maximum <= 0:
        raise ConfigurationError("Maximum marks must be greater than zero")
    if passing < 0 or passing > maximum:
        raise ConfigurationError("Passing marks must be between zero and maximum marks")
    if int(snapshot["valuation_rounds"]) not in {1, 2, 3}:
        raise ConfigurationError("Valuation rounds must be one, two or three")
    if discrepancy < 0 or discrepancy > maximum:
        raise ConfigurationError("Discrepancy threshold is outside the valid range")
    questions = snapshot.get("questions", [])
    if questions and sum((Decimal(str(item["max_marks"])) for item in questions), Decimal("0")) != maximum:
        raise ConfigurationError("Question marks must equal the paper maximum")


def _paper_field_value(field, value):
    if field in {"max_marks", "pass_marks", "discrepancy_threshold"}:
        return Decimal(str(value))
    if field == "valuation_rounds":
        return int(value)
    if field == "effective_from" and isinstance(value, str):
        return parse_datetime(value)
    return value


def paper_change_impact(paper):
    scripts = paper.scripts.all()
    assignments = sum(script.assignments.count() for script in scripts.prefetch_related("assignments"))
    submitted = sum(script.assignments.filter(status="submitted").count() for script in scripts.prefetch_related("assignments"))
    active = sum(script.assignments.exclude(status__in=["submitted", "expired"]).count() for script in scripts.prefetch_related("assignments"))
    return {
        "scripts": scripts.count(),
        "assignments": assignments,
        "active_assignments": active,
        "submitted_assignments": submitted,
        "dispatches": paper.dispatches.count(),
        "session_status": paper.session.status,
        "is_active_evaluation": paper.session.status == ExamSession.Status.ACTIVE or active > 0 or submitted > 0,
    }


@transaction.atomic
def update_paper(*, tenant_id, actor_id, paper_id, version, changes):
    paper = Paper.objects.select_for_update().select_related("session").prefetch_related("questions").filter(id=paper_id, tenant_id=tenant_id).first()
    if not paper:
        raise ConfigurationError("Paper was not found")
    if paper.version != version:
        raise ConfigurationConflict("Paper was changed by another user")
    impact = paper_change_impact(paper)
    if paper.status != Paper.Status.DRAFT or impact["is_active_evaluation"]:
        raise ConfigurationConflict("Approved, frozen or active papers require a dual-approved emergency change")
    values = _normalise_changes(changes)
    if not values:
        raise ConfigurationError("At least one paper field must be changed")
    snapshot = {**paper_snapshot(paper), **values}
    _validate_snapshot(snapshot)
    for field, value in values.items():
        setattr(paper, field, _paper_field_value(field, value))
    paper.version += 1
    paper.save(update_fields=[*values.keys(), "version", "updated_at"])
    ConfigurationRevision.objects.create(
        tenant_id=tenant_id,
        aggregate_type="Paper",
        aggregate_id=paper.id,
        version=paper.version,
        change_type=ConfigurationRevision.ChangeType.UPDATE,
        snapshot=paper_snapshot(paper),
        actor_id=actor_id,
        reason="Draft configuration updated",
        effective_from=paper.effective_from or timezone.now(),
    )
    record_event(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="config.paper.updated",
        aggregate="Paper",
        aggregate_id=paper.id,
        payload={"fields": sorted(values), "version": paper.version},
    )
    return paper


def _change_snapshot(paper, *, kind, changes, target_revision_version):
    if kind == ConfigurationChangeRequest.Kind.ROLLBACK:
        if target_revision_version is None:
            raise ConfigurationError("A rollback target revision is required")
        revision = ConfigurationRevision.objects.filter(
            tenant_id=paper.tenant_id,
            aggregate_type="Paper",
            aggregate_id=paper.id,
            version=target_revision_version,
        ).first()
        if not revision:
            raise ConfigurationError("Rollback revision was not found")
        return revision.snapshot
    if kind != ConfigurationChangeRequest.Kind.EMERGENCY_UPDATE:
        raise ConfigurationError("Change kind must be emergency_update or rollback")
    values = _normalise_changes(changes)
    if not values:
        raise ConfigurationError("Emergency changes cannot be empty")
    return {**paper_snapshot(paper), **values}


@transaction.atomic
def request_paper_change(*, tenant_id, actor_id, paper_id, version, kind, reason, changes, target_revision_version=None):
    paper = Paper.objects.select_for_update().select_related("session").prefetch_related("questions").filter(id=paper_id, tenant_id=tenant_id).first()
    if not paper:
        raise ConfigurationError("Paper was not found")
    if paper.version != version:
        raise ConfigurationConflict("Paper was changed by another user")
    if len(reason.strip()) < 12:
        raise ConfigurationError("A specific change reason of at least 12 characters is required")
    if paper.change_requests.filter(status=ConfigurationChangeRequest.Status.PENDING).exists():
        raise ConfigurationConflict("This paper already has a pending governed change")
    proposed = _change_snapshot(paper, kind=kind, changes=changes, target_revision_version=target_revision_version)
    _validate_snapshot(proposed)
    request = ConfigurationChangeRequest.objects.create(
        tenant_id=tenant_id,
        paper=paper,
        kind=kind,
        base_version=paper.version,
        target_revision_version=target_revision_version,
        proposed_changes=proposed,
        impact=paper_change_impact(paper),
        reason=reason.strip(),
        submitted_by_id=actor_id,
    )
    record_event(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="config.change.requested",
        aggregate="ConfigurationChangeRequest",
        aggregate_id=request.id,
        payload={"paper_id": str(paper.id), "kind": kind, "base_version": paper.version, "impact": request.impact},
    )
    return request


def _apply_change(request, actor_id):
    paper = Paper.objects.select_for_update().prefetch_related("questions").get(id=request.paper_id, tenant_id=request.tenant_id)
    if paper.version != request.base_version:
        raise ConfigurationConflict("The paper changed after this request was submitted; create a new request")
    snapshot = request.proposed_changes
    _validate_snapshot(snapshot)
    for field in PAPER_MUTABLE_FIELDS:
        if field in snapshot:
            setattr(paper, field, _paper_field_value(field, snapshot[field]))
    if request.kind == ConfigurationChangeRequest.Kind.ROLLBACK:
        paper.questions.all().delete()
        Question.objects.bulk_create(
            [
                Question(
                    tenant_id=request.tenant_id,
                    paper=paper,
                    number=item["number"],
                    max_marks=Decimal(str(item["max_marks"])),
                    required=item["required"],
                    position=item["position"],
                )
                for item in snapshot.get("questions", [])
            ]
        )
    paper.version += 1
    paper.save(update_fields=[*PAPER_MUTABLE_FIELDS, "version", "updated_at"])
    ConfigurationRevision.objects.create(
        tenant_id=request.tenant_id,
        aggregate_type="Paper",
        aggregate_id=paper.id,
        version=paper.version,
        change_type=ConfigurationRevision.ChangeType.ROLLBACK if request.kind == ConfigurationChangeRequest.Kind.ROLLBACK else ConfigurationRevision.ChangeType.EMERGENCY,
        snapshot=paper_snapshot(paper),
        actor_id=actor_id,
        reason=request.reason,
        effective_from=paper.effective_from or timezone.now(),
    )
    request.status = ConfigurationChangeRequest.Status.APPLIED
    request.applied_by_id = actor_id
    request.applied_at = timezone.now()
    return paper


@transaction.atomic
def decide_paper_change(*, tenant_id, actor_id, request_id, version, decision, note):
    change = ConfigurationChangeRequest.objects.select_for_update().filter(id=request_id, tenant_id=tenant_id).first()
    if not change:
        raise ConfigurationError("Configuration change request was not found")
    if change.version != version:
        raise ConfigurationConflict("Configuration change request was changed by another user")
    if change.status != ConfigurationChangeRequest.Status.PENDING:
        raise ConfigurationConflict("Configuration change request is already closed")
    if change.submitted_by_id == actor_id:
        raise ConfigurationConflict("The requester cannot approve their own configuration change")
    if decision not in ConfigurationChangeApproval.Decision.values:
        raise ConfigurationError("Decision must be approved or rejected")
    try:
        ConfigurationChangeApproval.objects.create(
            tenant_id=tenant_id,
            change_request=change,
            actor_id=actor_id,
            decision=decision,
            note=note,
        )
    except IntegrityError as exc:
        raise ConfigurationConflict("This administrator has already decided this change") from exc
    paper = None
    if decision == ConfigurationChangeApproval.Decision.REJECTED:
        change.status = ConfigurationChangeRequest.Status.REJECTED
    elif change.approvals.filter(decision=ConfigurationChangeApproval.Decision.APPROVED).count() >= change.required_approvals:
        paper = _apply_change(change, actor_id)
    change.version += 1
    change.save(update_fields=["status", "version", "applied_by_id", "applied_at", "updated_at"])
    record_event(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="config.change.applied" if change.status == ConfigurationChangeRequest.Status.APPLIED else "config.change.decided",
        aggregate="ConfigurationChangeRequest",
        aggregate_id=change.id,
        payload={"paper_id": str(change.paper_id), "decision": decision, "status": change.status, "version": change.version},
    )
    return change, paper


def change_request_rows(tenant_id):
    rows = []
    for item in ConfigurationChangeRequest.objects.filter(tenant_id=tenant_id).select_related("paper").prefetch_related("approvals"):
        rows.append(
            {
                "id": str(item.id),
                "paper_id": str(item.paper_id),
                "paper": item.paper.code,
                "kind": item.kind,
                "status": item.status,
                "base_version": item.base_version,
                "target_revision_version": item.target_revision_version,
                "reason": item.reason,
                "impact": item.impact,
                "submitted_by_id": item.submitted_by_id,
                "approval_count": item.approvals.filter(decision=ConfigurationChangeApproval.Decision.APPROVED).count(),
                "required_approvals": item.required_approvals,
                "version": item.version,
                "created_at": item.created_at.isoformat(),
            }
        )
    return rows


def configuration_readiness(tenant_id):
    sessions = ExamSession.objects.filter(tenant_id=tenant_id).order_by("-evaluation_starts_at")
    session = sessions.filter(status__in=[ExamSession.Status.READY, ExamSession.Status.ACTIVE]).first() or sessions.first()
    issues = []
    critical = []
    if not session:
        critical.append("No examination session is configured")
        return {"decision": "not_ready", "ready": False, "session": None, "issues": issues, "critical_alerts": critical}
    papers = Paper.objects.filter(tenant_id=tenant_id, session=session).select_related("session").prefetch_related("questions")
    if not papers.exists():
        critical.append("No papers are mapped to the examination session")
    for paper in papers:
        for issue in paper_readiness(paper)["issues"]:
            issues.append(f"{paper.code}: {issue}")
        if paper.status != Paper.Status.FROZEN:
            critical.append(f"{paper.code}: configuration is not frozen")
    if not EvaluationEvent.objects.filter(tenant_id=tenant_id, session=session, is_active=True).exists():
        issues.append("No active evaluation event is scheduled")
    if not EvaluationCentre.objects.filter(tenant_id=tenant_id, is_active=True).exists():
        critical.append("No active evaluation centre is configured")
    ready = not issues and not critical
    return {
        "decision": "go_live" if ready else "not_ready",
        "ready": ready,
        "session": {"id": str(session.id), "name": session.name, "status": session.status},
        "paper_count": papers.count(),
        "issues": issues,
        "critical_alerts": critical,
    }
