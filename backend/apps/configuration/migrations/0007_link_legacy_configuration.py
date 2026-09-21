from django.db import migrations


def link_legacy(apps, schema_editor):
    Term = apps.get_model("configuration", "Term")
    Regulation = apps.get_model("configuration", "Regulation")
    ExamSession = apps.get_model("configuration", "ExamSession")
    Programme = apps.get_model("configuration", "Programme")
    Subject = apps.get_model("configuration", "Subject")
    Paper = apps.get_model("configuration", "Paper")

    for session in ExamSession.objects.select_related("academic_year").filter(term_record__isnull=True).iterator():
        term = Term.objects.filter(tenant_id=session.tenant_id, academic_year_id=session.academic_year_id, name=session.term).first()
        if not term:
            last = Term.objects.filter(academic_year_id=session.academic_year_id).order_by("-sequence").first()
            term = Term.objects.create(
                tenant_id=session.tenant_id, academic_year_id=session.academic_year_id,
                name=session.term, sequence=(last.sequence if last else 0) + 1,
                starts_on=session.academic_year.starts_on, ends_on=session.academic_year.ends_on,
            )
        session.term_record_id = term.id
        session.save(update_fields=["term_record"])

    for programme in Programme.objects.filter(regulation_record__isnull=True).iterator():
        regulation = Regulation.objects.filter(tenant_id=programme.tenant_id, code=programme.regulation).first()
        if not regulation:
            regulation = Regulation.objects.create(
                tenant_id=programme.tenant_id, code=programme.regulation,
                title=f"Legacy regulation {programme.regulation}", effective_from="2000-01-01",
            )
        programme.regulation_record_id = regulation.id
        programme.save(update_fields=["regulation_record"])

    for subject in Subject.objects.all().iterator():
        linked = [str(value) for value in Paper.objects.filter(subject_id=subject.id).values_list("session_id", flat=True).distinct()]
        merged = list(dict.fromkeys([*subject.session_ids, *linked]))
        if merged != subject.session_ids:
            subject.session_ids = merged
            subject.save(update_fields=["session_ids"])


class Migration(migrations.Migration):
    dependencies = [("configuration", "0006_academicyear_is_active_academicyear_version_and_more")]
    operations = [migrations.RunPython(link_legacy, migrations.RunPython.noop)]
