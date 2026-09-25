from decimal import Decimal
from ipaddress import ip_network
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from apps.configuration.models import (
    AcademicYear,
    CalendarOverlapException,
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
            value = str(value) if value else None
        elif isinstance(value, Decimal):
            value = str(value)
        elif hasattr(value, "isoformat"):
            text = value.isoformat()
            if "T" in text:
                text = text.replace("T", " ")
            if text.endswith("Z"):
                text = text[:-1]
            value = text
        result[field] = value
    return result


def configuration_catalog(tenant_id):
    centre_names = {str(item.id): item.name for item in EvaluationCentre.objects.filter(tenant_id=tenant_id)}
    return {
        "academic_years": [_row(item, ("label", "starts_on", "ends_on", "is_active", "version")) for item in AcademicYear.objects.filter(tenant_id=tenant_id).order_by("-starts_on")],
        "regulations": [_row(item, ("code", "title", "effective_from", "effective_to", "is_active", "version")) for item in Regulation.objects.filter(tenant_id=tenant_id).order_by("code")],
        "terms": [{**_row(item, ("academic_year_id", "name", "sequence", "starts_on", "ends_on", "is_active", "version")), "academic_year": item.academic_year.label} for item in Term.objects.filter(tenant_id=tenant_id).select_related("academic_year").order_by("academic_year", "sequence")],
        "sessions": [_row(item, ("academic_year_id", "term_record_id", "name", "term", "evaluation_starts_at", "evaluation_ends_at", "status", "is_active", "version")) for item in ExamSession.objects.filter(tenant_id=tenant_id).order_by("-evaluation_starts_at")],
        "events": [{**_row(item, ("session_id", "name", "starts_at", "ends_at", "evaluation_centre_ids", "is_active", "version")), "session": item.session.name, "centres": ", ".join(centre_names.get(str(centre_id), "Missing centre") for centre_id in item.evaluation_centre_ids)} for item in EvaluationEvent.objects.filter(tenant_id=tenant_id).select_related("session").order_by("starts_at")],
        "programmes": [_row(item, ("code", "name", "regulation", "regulation_record_id", "is_active", "version")) for item in Programme.objects.filter(tenant_id=tenant_id).order_by("code")],
        "courses": [
            {
                **_row(item, ("programme_id", "regulation_id", "code", "name", "duration_terms", "is_active", "version")),
                "programme": item.programme.code,
                "regulation": item.regulation.code,
            }
            for item in Course.objects.filter(tenant_id=tenant_id).select_related("programme", "regulation").order_by("code")
        ],
        "subjects": [
            {
                **_row(item, ("programme_id", "course_id", "code", "name", "semester", "session_ids", "related_subject_ids", "is_active", "version")),
                "programme": item.programme.code,
                "course": item.course.code if item.course else "—",
            }
            for item in Subject.objects.filter(tenant_id=tenant_id).select_related("programme", "course").order_by("code")
        ],
        "centres": [_row(item, ("code", "name", "address", "network_cidrs", "is_active", "version")) for item in EvaluationCentre.objects.filter(tenant_id=tenant_id).order_by("code")],
        "papers": [paper_detail(item) for item in Paper.objects.filter(tenant_id=tenant_id).select_related("session", "subject").prefetch_related("questions", "approvals").order_by("code")],
        "change_requests": change_request_rows(tenant_id),
        "calendar_exceptions": [_row(item, ("entity", "academic_year_id", "target_id", "starts_on", "ends_on", "reason", "status", "requested_by_id", "decided_by_id")) for item in CalendarOverlapException.objects.filter(tenant_id=tenant_id).order_by("-created_at")[:100]],
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
        "submitted_by_id": paper.submitted_by_id,
        "approved_by_id": paper.approved_by_id,
        "frozen_by_id": paper.frozen_by_id,
        "effective_from": paper.effective_from.isoformat() if paper.effective_from else None,
        "questions": [
            {"number": question.number, "sub_question": question.sub_question, "max_marks": str(question.max_marks), "question_type": question.question_type, "required": question.required, "position": question.position}
            for question in paper.questions.all()
        ],
    }


def paper_readiness(paper):
    issues = []
    questions = list(paper.questions.all())
    if paper.max_marks <= 0:
        issues.append("Maximum marks must be greater than zero")
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
    try:
        _validate_second_valuation_threshold(paper.valuation_rounds, paper.max_marks, paper.rules)
    except ConfigurationError as exc:
        issues.append(str(exc))
    if paper.session.evaluation_starts_at >= paper.session.evaluation_ends_at:
        issues.append("Evaluation window must end after it starts")
    return {"ready": not issues, "issues": issues}


def paper_detail(paper):
    readiness = paper_readiness(paper)
    return {
        **_row(paper, ("session_id", "subject_id", "code", "title", "max_marks", "pass_marks", "valuation_rounds", "discrepancy_threshold", "moderation_required", "rules", "status", "version", "effective_from", "submitted_by_id", "approved_by_id", "frozen_by_id", "frozen_at")),
        "session": paper.session.name,
        "subject": paper.subject.code,
        "questions": [question_detail(item) for item in paper.questions.all()],
        "approval_count": paper.approvals.filter(decision=ConfigurationApproval.Decision.APPROVED).count(),
        "readiness": readiness,
    }


def question_detail(question):
    return _row(question, ("number", "sub_question", "max_marks", "question_type", "required", "position"))


def paper_for_configuration(tenant_id, paper_id):
    paper = Paper.objects.filter(id=paper_id, tenant_id=tenant_id).select_related("session", "subject").prefetch_related("questions", "approvals").first()
    if not paper:
        raise ConfigurationError("Paper was not found")
    return paper


def _related(model, tenant_id, item_id, label):
    try:
        item = model.objects.filter(id=UUID(str(item_id)), tenant_id=tenant_id).first()
    except (ValueError, TypeError):
        item = None
    if not item:
        raise ConfigurationError(f"{label} was not found in this university")
    return item


def _uuid_ids(values, label):
    try:
        return list(dict.fromkeys(str(UUID(str(value))) for value in values))
    except (ValueError, TypeError) as exc:
        raise ConfigurationError(f"{label} contains an invalid ID") from exc


def _uuid(value, label):
    try:
        return UUID(str(value))
    except (ValueError, TypeError) as exc:
        raise ConfigurationError(f"{label} ID is invalid") from exc


def _create_simple(*, model, tenant_id, actor_id, action, aggregate, values, idempotency_key=""):
    _validate_text_lengths(model, values)
    try:
        with transaction.atomic():
            record = None
            if idempotency_key:
                record, replay_id = begin_idempotent(tenant_id=tenant_id, scope=action, key=idempotency_key, payload={key: str(value.id) if hasattr(value, "id") else value for key, value in values.items()})
                if replay_id:
                    return model.objects.get(tenant_id=tenant_id, id=replay_id)
            item = model.objects.create(tenant_id=tenant_id, **values)
            _record_master_revision(item, actor_id, ConfigurationRevision.ChangeType.CREATE)
            record_event(tenant_id=tenant_id, actor_id=actor_id, action=action, aggregate=aggregate, aggregate_id=item.id, payload={"id": str(item.id)})
            if record:
                complete_idempotent(record, item.id)
            return item
    except IntegrityError as exc:
        raise ConfigurationConflict(f"{aggregate} already exists") from exc


def _validate_text_lengths(model, values):
    for name, value in values.items():
        field = model._meta.get_field(name)
        if isinstance(value, str) and field.max_length and len(value) > field.max_length:
            raise ConfigurationError(f"{name} must be at most {field.max_length} characters")


def _record_master_revision(item, actor_id, change_type, reason=""):
    fields = [field.attname for field in item._meta.concrete_fields if field.name not in {"id", "tenant_id", "created_at", "updated_at"}]
    ConfigurationRevision.objects.create(
        tenant_id=item.tenant_id, aggregate_type=type(item).__name__, aggregate_id=item.id,
        version=item.version, change_type=change_type, snapshot=_row(item, fields),
        actor_id=actor_id, reason=reason,
    )


def _no_overlap(model, tenant_id, starts_on, ends_on, *, parent_id=None, exclude_id=None):
    query = model.objects.filter(tenant_id=tenant_id, is_active=True, starts_on__lte=ends_on, ends_on__gte=starts_on)
    if parent_id is not None:
        query = query.filter(academic_year_id=parent_id)
    if exclude_id:
        query = query.exclude(id=exclude_id)
    if query.exists():
        exception = CalendarOverlapException.objects.select_for_update().filter(
            tenant_id=tenant_id, entity="academic_year" if model is AcademicYear else "term",
            academic_year_id=parent_id, target_id=exclude_id, starts_on=starts_on,
            ends_on=ends_on, status=CalendarOverlapException.Status.APPROVED,
        ).order_by("created_at").first()
        if not exception:
            raise ConfigurationConflict(f"{model.__name__} dates overlap an active record; request an approved calendar exception")
        exception.status = CalendarOverlapException.Status.USED
        exception.used_at = timezone.now()
        exception.save(update_fields=["status", "used_at", "updated_at"])


@transaction.atomic
def create_academic_year(*, tenant_id, actor_id, **values):
    if values["starts_on"] >= values["ends_on"]:
        raise ConfigurationError("Academic year end date must follow its start date")
    _no_overlap(AcademicYear, tenant_id, values["starts_on"], values["ends_on"])
    return _create_simple(model=AcademicYear, tenant_id=tenant_id, actor_id=actor_id, action="config.academic_year.created", aggregate="AcademicYear", values=values)


def create_regulation(*, tenant_id, actor_id, **values):
    if values.get("effective_to") and values["effective_from"] >= values["effective_to"]:
        raise ConfigurationError("Regulation end date must follow its effective date")
    return _create_simple(model=Regulation, tenant_id=tenant_id, actor_id=actor_id, action="config.regulation.created", aggregate="Regulation", values=values)


@transaction.atomic
def create_term(*, tenant_id, actor_id, academic_year_id, **values):
    year = _related(AcademicYear, tenant_id, academic_year_id, "Academic year")
    if values["starts_on"] < year.starts_on or values["ends_on"] > year.ends_on or values["starts_on"] >= values["ends_on"]:
        raise ConfigurationError("Term dates must be ordered and contained in the academic year")
    _no_overlap(Term, tenant_id, values["starts_on"], values["ends_on"], parent_id=year.id)
    return _create_simple(model=Term, tenant_id=tenant_id, actor_id=actor_id, action="config.term.created", aggregate="Term", values={"academic_year": year, **values})


def create_session(*, tenant_id, actor_id, academic_year_id, term_id, idempotency_key="", **values):
    year = _related(AcademicYear, tenant_id, academic_year_id, "Academic year")
    term = _related(Term, tenant_id, term_id, "Term")
    if term.academic_year_id != year.id or not term.is_active or not year.is_active:
        raise ConfigurationError("Select an active term in the selected academic year")
    if values["evaluation_starts_at"] >= values["evaluation_ends_at"]:
        raise ConfigurationError("Evaluation end time must follow its start time")
    if values["evaluation_starts_at"].date() < term.starts_on or values["evaluation_ends_at"].date() > term.ends_on:
        raise ConfigurationError("Evaluation window must be within the selected term")
    return _create_simple(model=ExamSession, tenant_id=tenant_id, actor_id=actor_id, action="config.session.created", aggregate="ExamSession", values={"academic_year": year, "term_record": term, "term": term.name, **values}, idempotency_key=idempotency_key)


def create_event(*, tenant_id, actor_id, session_id, evaluation_centre_ids, idempotency_key="", **values):
    session = _related(ExamSession, tenant_id, session_id, "Examination session")
    if values["starts_at"] >= values["ends_at"]:
        raise ConfigurationError("Evaluation event end time must follow its start time")
    if values["starts_at"] < session.evaluation_starts_at or values["ends_at"] > session.evaluation_ends_at:
        raise ConfigurationError("Evaluation event must be inside the session evaluation window")
    try:
        centre_ids = [str(UUID(str(item))) for item in evaluation_centre_ids]
    except (ValueError, TypeError) as exc:
        raise ConfigurationError("Evaluation centre IDs must be valid") from exc
    if values.get("is_active", True) and not centre_ids:
        raise ConfigurationError("An active evaluation event requires at least one assigned centre")
    if len(centre_ids) != len(set(centre_ids)):
        raise ConfigurationError("Evaluation centres must be unique")
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
        idempotency_key=idempotency_key,
    )


def create_programme(*, tenant_id, actor_id, regulation_id, **values):
    regulation = _related(Regulation, tenant_id, regulation_id, "Regulation")
    _require_effective_regulation(regulation)
    return _create_simple(model=Programme, tenant_id=tenant_id, actor_id=actor_id, action="config.programme.created", aggregate="Programme", values={**values, "regulation_record": regulation, "regulation": regulation.code})


def _require_effective_regulation(regulation):
    today = timezone.localdate()
    if not regulation.is_active or regulation.effective_from > today or (regulation.effective_to and regulation.effective_to < today):
        raise ConfigurationError("Regulation must be active and effective today")


def create_course(*, tenant_id, actor_id, programme_id, regulation_id, **values):
    programme = _related(Programme, tenant_id, programme_id, "Programme")
    regulation = _related(Regulation, tenant_id, regulation_id, "Regulation")
    _require_effective_regulation(regulation)
    if not programme.is_active or (programme.regulation_record_id and programme.regulation_record_id != regulation.id) or programme.regulation != regulation.code:
        raise ConfigurationError("Course regulation must match the active programme regulation")
    if values["duration_terms"] < 1:
        raise ConfigurationError("Course duration must be at least one term")
    return _create_simple(model=Course, tenant_id=tenant_id, actor_id=actor_id, action="config.course.created", aggregate="Course", values={"programme": programme, "regulation": regulation, **values})


def create_subject(*, tenant_id, actor_id, programme_id, course_id=None, session_ids=None, related_subject_ids=None, **values):
    programme = _related(Programme, tenant_id, programme_id, "Programme")
    course = _related(Course, tenant_id, course_id, "Course") if course_id else None
    if course and course.programme_id != programme.id:
        raise ConfigurationError("Course must belong to the selected programme")
    normalized_sessions = _uuid_ids(session_ids or [], "Available sessions")
    normalized_related = _uuid_ids(related_subject_ids or [], "Related subjects")
    if normalized_sessions and ExamSession.objects.filter(tenant_id=tenant_id, id__in=normalized_sessions).count() != len(normalized_sessions):
        raise ConfigurationError("Every selected session must belong to this university")
    related = list(Subject.objects.filter(tenant_id=tenant_id, id__in=normalized_related))
    if len(related) != len(normalized_related) or any(item.programme_id != programme.id or (course and item.course_id and item.course_id != course.id) for item in related):
        raise ConfigurationError("Related subjects must belong to the same programme and compatible course")
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
    _validate_cidrs(values.get("network_cidrs", []))
    return _create_simple(model=EvaluationCentre, tenant_id=tenant_id, actor_id=actor_id, action="config.centre.created", aggregate="EvaluationCentre", values=values)


def _validate_cidrs(cidrs):
    if not isinstance(cidrs, list):
        raise ConfigurationError("Approved networks must be a list of CIDR ranges")
    for cidr in cidrs:
        try:
            ip_network(cidr, strict=True)
        except ValueError as exc:
            raise ConfigurationError(f"Invalid approved network: {cidr}") from exc


@transaction.atomic
def request_calendar_exception(*, tenant_id, actor_id, entity, academic_year_id, target_id, starts_on, ends_on, reason):
    if entity not in {"academic_year", "term"} or starts_on >= ends_on or len(reason.strip()) < 12:
        raise ConfigurationError("Choose a valid calendar window and give a reason of at least 12 characters")
    year = _related(AcademicYear, tenant_id, academic_year_id, "Academic year") if entity == "term" else None
    if entity == "term" and (starts_on < year.starts_on or ends_on > year.ends_on):
        raise ConfigurationError("Term exception dates must fit inside the academic year")
    model = AcademicYear if entity == "academic_year" else Term
    target = _related(model, tenant_id, target_id, "Calendar record") if target_id else None
    if target and year and target.academic_year_id != year.id:
        raise ConfigurationError("Target term belongs to a different academic year")
    overlap = model.objects.filter(tenant_id=tenant_id, is_active=True, starts_on__lte=ends_on, ends_on__gte=starts_on)
    if year:
        overlap = overlap.filter(academic_year=year)
    if target:
        overlap = overlap.exclude(id=target.id)
    if not overlap.exists():
        raise ConfigurationError("No active calendar record overlaps this window")
    item = CalendarOverlapException.objects.create(
        tenant_id=tenant_id, entity=entity, academic_year=year, target_id=target.id if target else None,
        starts_on=starts_on, ends_on=ends_on, reason=reason.strip(), requested_by_id=actor_id,
    )
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="config.calendar_exception.requested", aggregate="CalendarOverlapException", aggregate_id=item.id, payload={"entity": entity, "starts_on": starts_on.isoformat(), "ends_on": ends_on.isoformat()})
    return item


@transaction.atomic
def decide_calendar_exception(*, tenant_id, actor_id, exception_id, approve):
    item = CalendarOverlapException.objects.select_for_update().filter(tenant_id=tenant_id, id=_uuid(exception_id, "Calendar exception")).first()
    if not item:
        raise ConfigurationError("Calendar exception was not found")
    if item.status != CalendarOverlapException.Status.PENDING:
        raise ConfigurationConflict("Calendar exception has already been decided")
    if item.requested_by_id == actor_id:
        raise ConfigurationConflict("A different administrator must approve this exception")
    item.status = CalendarOverlapException.Status.APPROVED if approve else CalendarOverlapException.Status.REJECTED
    item.decided_by_id = actor_id
    item.decided_at = timezone.now()
    item.save(update_fields=["status", "decided_by_id", "decided_at", "updated_at"])
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="config.calendar_exception.decided", aggregate="CalendarOverlapException", aggregate_id=item.id, payload={"status": item.status})
    return item


MASTER_MODELS = {
    "academic-years": (AcademicYear, {"label", "starts_on", "ends_on", "is_active"}),
    "regulations": (Regulation, {"code", "title", "effective_from", "effective_to", "is_active"}),
    "terms": (Term, {"name", "sequence", "starts_on", "ends_on", "is_active"}),
    "sessions": (ExamSession, {"name", "evaluation_starts_at", "evaluation_ends_at", "is_active"}),
    "events": (EvaluationEvent, {"name", "starts_at", "ends_at", "evaluation_centre_ids", "is_active"}),
    "programmes": (Programme, {"code", "name", "is_active"}),
    "courses": (Course, {"code", "name", "duration_terms", "is_active"}),
    "subjects": (Subject, {"code", "name", "semester", "session_ids", "related_subject_ids", "is_active"}),
    "centres": (EvaluationCentre, {"code", "name", "address", "network_cidrs", "is_active"}),
}


def _master_dependencies(item):
    if isinstance(item, AcademicYear):
        return item.terms.exists() or item.sessions.exists()
    if isinstance(item, Regulation):
        return item.programmes.exists() or item.courses.exists()
    if isinstance(item, Term):
        return item.sessions.exists()
    if isinstance(item, ExamSession):
        return item.events.filter(is_active=True).exists() or item.papers.exists()
    if isinstance(item, Programme):
        return item.courses.filter(is_active=True).exists() or item.subjects.filter(is_active=True).exists()
    if isinstance(item, Course):
        return item.subjects.filter(is_active=True).exists()
    if isinstance(item, Subject):
        return item.papers.exists()
    if isinstance(item, EvaluationEvent):
        return item.session.status == ExamSession.Status.ACTIVE and not item.session.events.filter(is_active=True).exclude(id=item.id).exists()
    if isinstance(item, EvaluationCentre):
        return any(str(item.id) in event.evaluation_centre_ids for event in EvaluationEvent.objects.filter(tenant_id=item.tenant_id, is_active=True).only("evaluation_centre_ids"))
    return False


def _validate_master(item):
    if isinstance(item, AcademicYear):
        if item.starts_on >= item.ends_on:
            raise ConfigurationError("Academic year dates must be ordered")
        if item.is_active:
            _no_overlap(AcademicYear, item.tenant_id, item.starts_on, item.ends_on, exclude_id=item.id)
        if item.terms.filter(starts_on__lt=item.starts_on).exists() or item.terms.filter(ends_on__gt=item.ends_on).exists():
            raise ConfigurationConflict("Existing terms must remain within the academic year")
    elif isinstance(item, Regulation):
        if item.effective_to and item.effective_from >= item.effective_to:
            raise ConfigurationError("Regulation dates must be ordered")
    elif isinstance(item, Term):
        if item.starts_on < item.academic_year.starts_on or item.ends_on > item.academic_year.ends_on or item.starts_on >= item.ends_on:
            raise ConfigurationError("Term dates must fit inside the academic year")
        if item.is_active:
            _no_overlap(Term, item.tenant_id, item.starts_on, item.ends_on, parent_id=item.academic_year_id, exclude_id=item.id)
        if item.sessions.filter(evaluation_starts_at__date__lt=item.starts_on).exists() or item.sessions.filter(evaluation_ends_at__date__gt=item.ends_on).exists():
            raise ConfigurationConflict("Existing sessions must remain within the term")
    elif isinstance(item, ExamSession):
        if item.evaluation_starts_at >= item.evaluation_ends_at or not item.term_record or item.term_record.academic_year_id != item.academic_year_id or item.evaluation_starts_at.date() < item.term_record.starts_on or item.evaluation_ends_at.date() > item.term_record.ends_on:
            raise ConfigurationError("Session evaluation window must fit inside its configured term")
        if item.events.filter(starts_at__lt=item.evaluation_starts_at).exists() or item.events.filter(ends_at__gt=item.evaluation_ends_at).exists():
            raise ConfigurationConflict("Existing events must remain within the session window")
    elif isinstance(item, EvaluationEvent):
        if item.starts_at >= item.ends_at or item.starts_at < item.session.evaluation_starts_at or item.ends_at > item.session.evaluation_ends_at:
            raise ConfigurationError("Event must fit inside the session evaluation window")
        ids = _uuid_ids(item.evaluation_centre_ids, "Evaluation centres")
        if item.is_active and not ids:
            raise ConfigurationError("Active events require at least one centre")
        if item.is_active and (len(ids) != len(set(ids)) or EvaluationCentre.objects.filter(tenant_id=item.tenant_id, id__in=ids, is_active=True).count() != len(ids)):
            raise ConfigurationError("Every assigned centre must be unique, active and in this university")
    elif isinstance(item, Course):
        if item.duration_terms < 1:
            raise ConfigurationError("Course duration must be at least one term")
        if item.is_active:
            _require_effective_regulation(item.regulation)
            if item.programme.regulation_record_id != item.regulation_id:
                raise ConfigurationError("Course regulation must match programme regulation")
    elif isinstance(item, Subject):
        if item.semester < 1:
            raise ConfigurationError("Semester must be at least one")
        item.related_subject_ids = _uuid_ids(item.related_subject_ids, "Related subjects")
        item.session_ids = _uuid_ids(item.session_ids, "Available sessions")
        related = list(Subject.objects.filter(tenant_id=item.tenant_id, id__in=item.related_subject_ids))
        if str(item.id) in item.related_subject_ids or len(related) != len(item.related_subject_ids) or any(other.programme_id != item.programme_id or (item.course_id and other.course_id and other.course_id != item.course_id) for other in related):
            raise ConfigurationError("Related subjects must be different and in the same programme and compatible course")
        if ExamSession.objects.filter(tenant_id=item.tenant_id, id__in=item.session_ids).count() != len(item.session_ids):
            raise ConfigurationError("Every available session must belong to this university")
    elif isinstance(item, EvaluationCentre):
        _validate_cidrs(item.network_cidrs)


@transaction.atomic
def update_master(*, tenant_id, actor_id, entity, item_id, version, changes, reason):
    if entity not in MASTER_MODELS:
        raise ConfigurationError("Unknown master record")
    model, allowed = MASTER_MODELS[entity]
    item = model.objects.select_for_update().filter(tenant_id=tenant_id, id=_uuid(item_id, "Master record")).first()
    if not item:
        raise ConfigurationError("Master record was not found")
    if item.version != version:
        raise ConfigurationConflict("Record was changed by another user")
    if not changes or set(changes) - allowed:
        raise ConfigurationError("No supported fields were provided")
    if len(reason.strip()) < 8:
        raise ConfigurationError("Give a reason of at least 8 characters")
    if changes.get("is_active") is False and _master_dependencies(item):
        raise ConfigurationConflict("This record has active dependencies and cannot be retired")
    _validate_text_lengths(model, changes)
    for field, value in changes.items():
        model_field = item._meta.get_field(field)
        try:
            value = model_field.to_python(value)
        except (ValidationError, ValueError, TypeError) as exc:
            raise ConfigurationError(f"Invalid value for {field}") from exc
        if value is None and not model_field.null:
            raise ConfigurationError(f"{field} is required")
        if field in {"evaluation_centre_ids", "session_ids", "related_subject_ids"} and not isinstance(value, list):
            raise ConfigurationError(f"{field} must be a list")
        setattr(item, field, value)
    _validate_master(item)
    item.version += 1
    try:
        item.save(update_fields=[*changes, "version", "updated_at"])
    except IntegrityError as exc:
        raise ConfigurationConflict("A record with this identity already exists") from exc
    _record_master_revision(item, actor_id, ConfigurationRevision.ChangeType.UPDATE, reason.strip())
    record_event(tenant_id=tenant_id, actor_id=actor_id, action=f"config.{model.__name__.lower()}.updated", aggregate=model.__name__, aggregate_id=item.id, payload={"version": item.version, "fields": sorted(changes), "reason": reason.strip()})
    return item


@transaction.atomic
def transition_session(*, tenant_id, actor_id, session_id, version, target, reason):
    session = ExamSession.objects.select_for_update().filter(tenant_id=tenant_id, id=_uuid(session_id, "Session")).first()
    if not session:
        raise ConfigurationError("Session was not found")
    if session.version != version:
        raise ConfigurationConflict("Session was changed by another user")
    next_status = {"draft": "approval", "approval": "ready", "ready": "active", "active": "closed"}
    if next_status.get(session.status) != target:
        raise ConfigurationConflict("Invalid session status transition")
    if target == "ready":
        submission = ConfigurationRevision.objects.filter(tenant_id=tenant_id, aggregate_type="ExamSession", aggregate_id=session.id).order_by("-version").first()
        if submission and submission.snapshot.get("status") == ExamSession.Status.APPROVAL and submission.actor_id == actor_id:
            raise ConfigurationConflict("A different administrator must approve session readiness")
    if target in {"ready", "active"}:
        papers = list(session.papers.all())
        if not papers or any(paper.status != Paper.Status.FROZEN or not paper_readiness(paper)["ready"] for paper in papers):
            raise ConfigurationConflict("Freeze all ready papers before this session transition")
        events = list(session.events.filter(is_active=True))
        active_centres = {str(value) for value in EvaluationCentre.objects.filter(tenant_id=tenant_id, is_active=True).values_list("id", flat=True)}
        if not events or any(not event.evaluation_centre_ids or any(str(value) not in active_centres for value in event.evaluation_centre_ids) for event in events):
            raise ConfigurationConflict("Assign active centres to every active event before this session transition")
    session.status = target
    session.version += 1
    session.save(update_fields=["status", "version", "updated_at"])
    _record_master_revision(session, actor_id, ConfigurationRevision.ChangeType.UPDATE, reason or f"Transitioned to {target}")
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="config.session.transitioned", aggregate="ExamSession", aggregate_id=session.id, payload={"status": target, "version": session.version})
    return session


@transaction.atomic
def create_paper(*, tenant_id, actor_id, session_id, subject_id, **values):
    session = _related(ExamSession, tenant_id, session_id, "Examination session")
    subject = _related(Subject, tenant_id, subject_id, "Subject")
    if not subject.is_active or not subject.session_ids or str(session.id) not in subject.session_ids:
        raise ConfigurationError("Subject must explicitly list this examination session as available")
    _validate_snapshot({**values, "questions": [], "discrepancy_threshold": values.get("discrepancy_threshold", 0), "valuation_rounds": values.get("valuation_rounds", 1)})
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
    values = _question_values(values)
    if values["position"] is None:
        values["position"] = max((item.position for item in paper.questions.all()), default=0) + 1
    try:
        question = Question.objects.create(tenant_id=tenant_id, paper=paper, **values)
    except IntegrityError as exc:
        raise ConfigurationConflict("Question number already exists in this paper") from exc
    paper.version += 1
    paper.save(update_fields=["version", "updated_at"])
    ConfigurationRevision.objects.create(tenant_id=tenant_id, aggregate_type="Paper", aggregate_id=paper.id, version=paper.version, change_type=ConfigurationRevision.ChangeType.UPDATE, snapshot=paper_snapshot(paper), actor_id=actor_id, reason="Question added")
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="config.question.created", aggregate="Question", aggregate_id=question.id, payload={"paper_id": str(paper.id), "paper_version": paper.version})
    return question, paper


def _question_values(values):
    result = dict(values)
    result["number"] = str(result["number"]).strip()
    result["sub_question"] = str(result.get("sub_question") or "").strip()
    if not result["number"] or len(result["number"]) > 16 or len(result["sub_question"]) > 8:
        raise ConfigurationError("Question number or sub-question is invalid")
    if result.get("question_type", Question.Type.DESCRIPTIVE) not in Question.Type.values:
        raise ConfigurationError("Question type is invalid")
    if result["max_marks"] < 0:
        raise ConfigurationError("Question marks cannot be negative")
    if result.get("position") is not None and not 1 <= result["position"] <= 32767:
        raise ConfigurationError("Question position is invalid")
    return result


@transaction.atomic
def update_question(*, tenant_id, actor_id, paper_id, question_id, version, values):
    paper = Paper.objects.select_for_update().filter(id=paper_id, tenant_id=tenant_id).first()
    if not paper:
        raise ConfigurationError("Paper was not found")
    if paper.version != version:
        raise ConfigurationConflict("Paper was changed by another user")
    if paper.status != Paper.Status.DRAFT:
        raise ConfigurationConflict("Questions can only be changed while a paper is in Draft")
    question = Question.objects.filter(id=question_id, paper=paper, tenant_id=tenant_id).first()
    if not question:
        raise ConfigurationError("Question was not found in this paper")
    values = _question_values(values)
    if values["position"] is None:
        values["position"] = question.position
    for field, value in values.items():
        setattr(question, field, value)
    try:
        question.save(update_fields=[*values, "updated_at"])
    except IntegrityError as exc:
        raise ConfigurationConflict("Question number already exists in this paper") from exc
    paper.version += 1
    paper.save(update_fields=["version", "updated_at"])
    ConfigurationRevision.objects.create(tenant_id=tenant_id, aggregate_type="Paper", aggregate_id=paper.id, version=paper.version, change_type=ConfigurationRevision.ChangeType.UPDATE, snapshot=paper_snapshot(paper), actor_id=actor_id, reason="Question updated")
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="config.question.updated", aggregate="Question", aggregate_id=question.id, payload={"paper_id": str(paper.id), "paper_version": paper.version})
    return question, paper


@transaction.atomic
def delete_question(*, tenant_id, actor_id, paper_id, question_id, version):
    paper = Paper.objects.select_for_update().filter(id=paper_id, tenant_id=tenant_id).first()
    if not paper:
        raise ConfigurationError("Paper was not found")
    if paper.version != version:
        raise ConfigurationConflict("Paper was changed by another user")
    if paper.status != Paper.Status.DRAFT:
        raise ConfigurationConflict("Questions can only be changed while a paper is in Draft")
    question = Question.objects.filter(id=question_id, paper=paper, tenant_id=tenant_id).first()
    if not question:
        raise ConfigurationError("Question was not found in this paper")
    question_id = question.id
    question.delete()
    paper.version += 1
    paper.save(update_fields=["version", "updated_at"])
    ConfigurationRevision.objects.create(tenant_id=tenant_id, aggregate_type="Paper", aggregate_id=paper.id, version=paper.version, change_type=ConfigurationRevision.ChangeType.UPDATE, snapshot=paper_snapshot(paper), actor_id=actor_id, reason="Question deleted")
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="config.question.deleted", aggregate="Question", aggregate_id=question_id, payload={"paper_id": str(paper.id), "paper_version": paper.version})
    return paper


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
    paper.submitted_by_id = actor_id
    paper.submitted_at = timezone.now()
    paper.approved_by_id = None
    paper.frozen_by_id = None
    paper.frozen_at = None
    paper.version += 1
    paper.save(update_fields=["status", "submitted_by_id", "submitted_at", "approved_by_id", "frozen_by_id", "frozen_at", "version", "updated_at"])
    ConfigurationRevision.objects.create(tenant_id=tenant_id, aggregate_type="Paper", aggregate_id=paper.id, version=paper.version, change_type=ConfigurationRevision.ChangeType.UPDATE, snapshot=paper_snapshot(paper), actor_id=actor_id, reason=note or "Submitted for approval")
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="config.paper.submitted", aggregate="Paper", aggregate_id=paper.id, payload={"version": paper.version, "submitted_by_id": actor_id})
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
    if paper.submitted_by_id == actor_id:
        raise ConfigurationConflict("A different administrator must approve the submitted paper")
    current_approvals = paper.approvals.filter(created_at__gte=paper.submitted_at) if paper.submitted_at else paper.approvals.all()
    if current_approvals.filter(actor_id=actor_id, decision=ConfigurationApproval.Decision.APPROVED).exists():
        raise ConfigurationConflict("This administrator has already approved the paper")
    stage = paper.approvals.count() + 1
    ConfigurationApproval.objects.create(tenant_id=tenant_id, paper=paper, stage=stage, decision=ConfigurationApproval.Decision.APPROVED, actor_id=actor_id, note=note)
    required = 2 if paper.rules.get("critical_change", False) else 1
    if current_approvals.filter(decision=ConfigurationApproval.Decision.APPROVED).count() >= required:
        paper.status = Paper.Status.APPROVED
        paper.approved_by_id = actor_id
    paper.version += 1
    paper.save(update_fields=["status", "approved_by_id", "version", "updated_at"])
    ConfigurationRevision.objects.create(tenant_id=tenant_id, aggregate_type="Paper", aggregate_id=paper.id, version=paper.version, change_type=ConfigurationRevision.ChangeType.APPROVE, snapshot=paper_snapshot(paper), actor_id=actor_id, reason=note)
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="config.paper.approved", aggregate="Paper", aggregate_id=paper.id, payload={"stage": stage, "required": required, "version": paper.version, "submitted_by_id": paper.submitted_by_id, "approved_by_id": actor_id})
    return paper


@transaction.atomic
def freeze_paper(*, tenant_id, actor_id, paper_id, version, note):
    paper = Paper.objects.select_for_update().prefetch_related("questions", "approvals").filter(id=paper_id, tenant_id=tenant_id).first()
    if not paper:
        raise ConfigurationError("Paper was not found")
    if paper.version != version:
        raise ConfigurationConflict("Paper was changed by another user")
    if paper.status != Paper.Status.APPROVED:
        raise ConfigurationConflict("Only approved papers can be frozen")
    if paper.submitted_by_id == actor_id:
        raise ConfigurationConflict("The submitting administrator cannot freeze this paper")
    current_approvals = paper.approvals.filter(created_at__gte=paper.submitted_at) if paper.submitted_at else paper.approvals.all()
    if current_approvals.filter(actor_id=actor_id, decision=ConfigurationApproval.Decision.APPROVED).exists():
        raise ConfigurationConflict("The approving administrator cannot freeze the same paper")
    paper.status = Paper.Status.FROZEN
    paper.frozen_by_id = actor_id
    paper.frozen_at = timezone.now()
    paper.version += 1
    paper.save(update_fields=["status", "frozen_by_id", "frozen_at", "version", "updated_at"])
    ConfigurationRevision.objects.create(tenant_id=tenant_id, aggregate_type="Paper", aggregate_id=paper.id, version=paper.version, change_type=ConfigurationRevision.ChangeType.FREEZE, snapshot=paper_snapshot(paper), actor_id=actor_id, reason=note)
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="config.paper.frozen", aggregate="Paper", aggregate_id=paper.id, payload={"version": paper.version, "submitted_by_id": paper.submitted_by_id, "approved_by_id": paper.approved_by_id, "frozen_by_id": actor_id})
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
    unknown = set(changes) - PAPER_MUTABLE_FIELDS - {"questions"}
    if unknown:
        raise ConfigurationError(f"Unsupported paper fields: {', '.join(sorted(unknown))}")
    result = dict(changes)
    for field in ("max_marks", "pass_marks", "discrepancy_threshold"):
        if field in result:
            result[field] = str(Decimal(str(result[field])))
    if "effective_from" in result and result["effective_from"] is not None:
        value = result["effective_from"]
        result["effective_from"] = value.isoformat() if hasattr(value, "isoformat") else str(value)
    if "questions" in result:
        if not isinstance(result["questions"], list) or not result["questions"]:
            raise ConfigurationError("Provide a non-empty question list")
        normalized = []
        seen = set()
        for position, question in enumerate(result["questions"], 1):
            if not isinstance(question, dict):
                raise ConfigurationError("Each question must be an object")
            values = _question_values({"number": question.get("number", ""), "sub_question": question.get("sub_question", ""), "max_marks": Decimal(str(question.get("max_marks", 0))), "question_type": question.get("question_type", Question.Type.DESCRIPTIVE), "required": question.get("required", True), "position": position})
            key = values["number"], values["sub_question"]
            if key in seen:
                raise ConfigurationError("Question numbers must be unique")
            seen.add(key)
            normalized.append({**values, "max_marks": str(values["max_marks"])})
        result["questions"] = normalized
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
    _validate_second_valuation_threshold(int(snapshot["valuation_rounds"]), maximum, snapshot.get("rules") or {})
    questions = snapshot.get("questions", [])
    if questions and sum((Decimal(str(item["max_marks"])) for item in questions), Decimal("0")) != maximum:
        raise ConfigurationError("Question marks must equal the paper maximum")


def _validate_second_valuation_threshold(rounds, maximum, rules):
    value = rules.get("second_valuation_mark_threshold")
    if value is None or value == "":
        return
    try:
        threshold = Decimal(str(value))
    except (ValueError, ArithmeticError) as exc:
        raise ConfigurationError("Round 2 score threshold must be a valid mark") from exc
    if rounds != 1 or not threshold.is_finite() or threshold < 0 or threshold >= maximum:
        raise ConfigurationError("Round 2 score threshold is only valid for one-round papers and must be below maximum marks")


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
    if "questions" in values:
        raise ConfigurationError("Change draft questions using the question editor")
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
    impact = paper_change_impact(paper)
    if proposed.get("questions") != paper_snapshot(paper).get("questions") and (impact["active_assignments"] or impact["submitted_assignments"]):
        raise ConfigurationConflict("Question content cannot change after evaluation has started")
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
    questions_changed = snapshot.get("questions") != paper_snapshot(paper).get("questions")
    if questions_changed:
        paper.questions.all().delete()
        Question.objects.bulk_create(
            [
                Question(
                    tenant_id=request.tenant_id,
                    paper=paper,
                    number=item["number"],
                    sub_question=item.get("sub_question", ""),
                    max_marks=Decimal(str(item["max_marks"])),
                    question_type=item.get("question_type", Question.Type.DESCRIPTIVE),
                    required=item["required"],
                    position=item["position"],
                )
                for item in snapshot.get("questions", [])
            ]
        )
        paper.status = Paper.Status.DRAFT
        paper.submitted_by_id = None
        paper.submitted_at = None
        paper.approved_by_id = None
        paper.frozen_by_id = None
        paper.frozen_at = None
    paper.version += 1
    paper.save(update_fields=[*PAPER_MUTABLE_FIELDS, "status", "submitted_by_id", "submitted_at", "approved_by_id", "frozen_by_id", "frozen_at", "version", "updated_at"])
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
    session = sessions.first()
    issues = []
    critical = []
    if not session:
        critical.append("No examination session is configured")
        return {"decision": "not_ready", "ready": False, "session": None, "issues": issues, "critical_alerts": critical}
    if session.status not in {ExamSession.Status.READY, ExamSession.Status.ACTIVE} or not session.is_active:
        critical.append(f"{session.name}: session must be Ready or Active before go-live")
    papers = Paper.objects.filter(tenant_id=tenant_id, session=session).select_related("session").prefetch_related("questions")
    if not papers.exists():
        critical.append("No papers are mapped to the examination session")
    for paper in papers:
        for issue in paper_readiness(paper)["issues"]:
            issues.append(f"{paper.code}: {issue}")
        if paper.status != Paper.Status.FROZEN:
            critical.append(f"{paper.code}: configuration is not frozen")
    events = list(EvaluationEvent.objects.filter(tenant_id=tenant_id, session=session, is_active=True))
    if not events:
        critical.append("No active evaluation event is scheduled")
    active_centres = {str(item) for item in EvaluationCentre.objects.filter(tenant_id=tenant_id, is_active=True).values_list("id", flat=True)}
    for event in events:
        assigned = event.evaluation_centre_ids
        if not assigned or len(assigned) != len(set(assigned)) or any(str(item) not in active_centres for item in assigned):
            critical.append(f"{event.name}: assign at least one active evaluation centre")
    ready = not issues and not critical
    return {
        "decision": "go_live" if ready else "not_ready",
        "ready": ready,
        "session": {"id": str(session.id), "name": session.name, "status": session.status},
        "paper_count": papers.count(),
        "issues": issues,
        "critical_alerts": critical,
    }
