import hashlib
import json
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from ninja.errors import HttpError

from apps.core.services import record_event
from apps.custody.models import Script
from apps.custody.services import transition_script
from apps.repository.models import ScriptAsset
from apps.repository.storage import process_scan_object, read_object_metadata
from apps.scanning.models import ScanJob

from .models import ProcessedPage, ProcessingProfile, ProcessingRun, ScanQualityException


RUN_TRANSITIONS = {
    ProcessingRun.Status.QUEUED: {ProcessingRun.Status.PROCESSING, ProcessingRun.Status.SKIPPED},
    ProcessingRun.Status.PROCESSING: {ProcessingRun.Status.QUALITY_REVIEW, ProcessingRun.Status.FAILED},
    ProcessingRun.Status.QUALITY_REVIEW: {ProcessingRun.Status.PASSED, ProcessingRun.Status.RETURNED, ProcessingRun.Status.FAILED},
    ProcessingRun.Status.RETURNED: {ProcessingRun.Status.PROCESSING, ProcessingRun.Status.SKIPPED},
    ProcessingRun.Status.FAILED: {ProcessingRun.Status.PROCESSING, ProcessingRun.Status.SKIPPED},
}

EXCEPTION_TRANSITIONS = {
    ScanQualityException.Status.OPEN: {
        ScanQualityException.Status.RETURNED_SCANNING,
        ScanQualityException.Status.RETURNED_VERIFICATION,
        ScanQualityException.Status.REPROCESSING,
        ScanQualityException.Status.RESOLVED,
        ScanQualityException.Status.SKIPPED,
    },
    ScanQualityException.Status.RETURNED_SCANNING: {ScanQualityException.Status.REPROCESSING, ScanQualityException.Status.RESOLVED},
    ScanQualityException.Status.RETURNED_VERIFICATION: {ScanQualityException.Status.REPROCESSING, ScanQualityException.Status.RESOLVED},
    ScanQualityException.Status.REPROCESSING: {ScanQualityException.Status.RESOLVED, ScanQualityException.Status.OPEN},
}


def _digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


@transaction.atomic
def create_profile(*, tenant_id, actor_id, code, name, configuration):
    required = {"resolution_dpi", "minimum_quality_score"}
    if not required.issubset(configuration):
        raise HttpError(422, "Processing profile requires resolution and quality thresholds")
    previous = ProcessingProfile.objects.filter(tenant_id=tenant_id, code=code.strip().upper()).order_by("-version").first()
    if previous:
        previous.is_active = False
        previous.save(update_fields=["is_active", "updated_at"])
    profile = ProcessingProfile.objects.create(
        tenant_id=tenant_id,
        code=code.strip().upper(),
        name=name.strip(),
        configuration=configuration,
        version=(previous.version + 1) if previous else 1,
    )
    record_event(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="scan_processing.profile.created",
        aggregate="ProcessingProfile",
        aggregate_id=profile.id,
        payload={"code": profile.code, "version": profile.version},
    )
    return profile


@transaction.atomic
def queue_run(*, tenant_id, actor_id, scan_job, profile):
    if scan_job.status != ScanJob.Status.COMPLETED or not scan_job.source_manifest:
        raise HttpError(409, "Only a completed scan job with source evidence can be processed")
    run = ProcessingRun.objects.create(
        tenant_id=tenant_id,
        script=scan_job.script,
        scan_job=scan_job,
        profile=profile,
        source_digest=_digest(scan_job.source_manifest),
    )
    record_event(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="scan_processing.run.queued",
        aggregate="ProcessingRun",
        aggregate_id=run.id,
        payload={"scan_job_id": str(scan_job.id), "profile": profile.code},
    )
    return run


def execute_run(*, tenant_id, actor_id, run_id, expected_version):
    with transaction.atomic():
        run = ProcessingRun.objects.select_for_update().select_related("script", "scan_job", "profile").filter(id=run_id, tenant_id=tenant_id).first()
        if not run or run.version != expected_version:
            raise HttpError(409, "Processing run is missing or stale")
        if ProcessingRun.Status.PROCESSING not in RUN_TRANSITIONS.get(run.status, set()):
            raise HttpError(409, "Processing run cannot be started from its current state")
        run.status = ProcessingRun.Status.PROCESSING
        run.current_stage = "image_enhancement"
        run.started_at = timezone.now()
        run.version += 1
        run.save()
        record_event(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="scan_processing.run.started",
            aggregate="ProcessingRun",
            aggregate_id=run.id,
            payload={"pages": len(run.scan_job.source_manifest)},
        )

    processed = []
    config = dict(run.profile.configuration)
    try:
        for index, source in enumerate(run.scan_job.source_manifest, 1):
            source_key = source["storage_key"]
            source_meta = read_object_metadata(source_key)
            if source_meta.sha256 != source["sha256"]:
                raise HttpError(409, f"Source checksum mismatch on page {index}")
            version = ScriptAsset.objects.filter(script=run.script, kind=ScriptAsset.Kind.MASTER, page_number=index).count() + 1
            output_key = f"scripts-master/{tenant_id}/{run.script_id}/{run.id}/page-{index}-v{version}.webp"
            result = process_scan_object(
                source_key=source_key,
                destination={"key": output_key, "mime_type": "image/webp"},
                configuration={**config, "mime_type": "image/webp"},
            )
            metrics = result["metrics"]
            processed.append({
                "index": index,
                "source": source,
                "source_meta": source_meta,
                "output": result["object"],
                "metrics": metrics,
                "barcode": str(source.get("barcode", ""))[:64],
                "qr_code": str(source.get("qr_code", ""))[:160],
                "recognized_page_number": source.get("page_number", index),
                "is_supplement": bool(source.get("is_supplement", False)),
            })
    except Exception as exc:
        with transaction.atomic():
            failed = ProcessingRun.objects.select_for_update().get(id=run.id)
            failed.status = ProcessingRun.Status.FAILED
            failed.current_stage = "failed"
            failed.version += 1
            failed.completed_at = timezone.now()
            failed.metrics = {"error": str(exc)[:300]}
            failed.save()
            record_event(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="scan_processing.run.failed",
                aggregate="ProcessingRun",
                aggregate_id=failed.id,
                payload={"reason": str(exc)[:300]},
            )
        raise

    with transaction.atomic():
        current = ProcessingRun.objects.select_for_update().select_related("script").get(id=run.id)
        if current.status != ProcessingRun.Status.PROCESSING:
            raise HttpError(409, "Processing run state changed while images were being processed")
        minimum = Decimal(str(config.get("minimum_quality_score", 65)))
        page_numbers = [item["recognized_page_number"] for item in processed]
        duplicates = {number for number in page_numbers if page_numbers.count(number) > 1}
        expected_pages = current.scan_job.expected_pages or len(processed)
        missing = sorted(set(range(1, expected_pages + 1)) - set(page_numbers))
        for item in processed:
            metrics = item["metrics"]
            page = ProcessedPage.objects.create(
                tenant_id=tenant_id,
                run=current,
                page_index=item["index"],
                recognized_page_number=item["recognized_page_number"],
                source_key=item["source"]["storage_key"],
                output_key=item["output"]["key"],
                source_sha256=item["source_meta"].sha256,
                output_sha256=item["output"]["sha256"],
                barcode=item["barcode"],
                qr_code=item["qr_code"],
                is_blank=metrics["is_blank"],
                is_supplement=item["is_supplement"],
                is_duplicate=item["recognized_page_number"] in duplicates,
                quality_score=Decimal(str(metrics["quality_score"])),
                resolution_dpi=metrics["resolution_dpi"],
                rotation_degrees=metrics["rotation_degrees"],
                processing_metadata={key: value for key, value in metrics.items() if key not in {"is_blank", "quality_score"}},
            )
            ScriptAsset.objects.create(
                tenant_id=tenant_id,
                script=current.script,
                kind=ScriptAsset.Kind.MASTER,
                page_number=item["index"],
                storage_key=item["output"]["key"],
                sha256=item["output"]["sha256"],
                byte_size=item["output"]["byte_size"],
                mime_type=item["output"]["mime_type"],
                version=ScriptAsset.objects.filter(script=current.script, kind=ScriptAsset.Kind.MASTER, page_number=item["index"]).count() + 1,
                backup_status="completed",
                replication_status="completed",
            )
            problems = []
            if page.quality_score < minimum:
                problems.append(("low_quality", "high", f"Quality score {page.quality_score} is below {minimum}"))
            if page.is_blank:
                problems.append(("blank_page", "medium", "Page appears blank"))
            if page.is_duplicate:
                problems.append(("duplicate_page", "high", "Recognized page number is duplicated"))
            for kind, severity, reason in problems:
                ScanQualityException.objects.create(tenant_id=tenant_id, run=current, page=page, kind=kind, severity=severity, reason=reason)
        for page_number in missing:
            ScanQualityException.objects.create(tenant_id=tenant_id, run=current, kind="missing_page", severity="high", reason=f"Expected page {page_number} was not detected")
        current.status = ProcessingRun.Status.QUALITY_REVIEW if current.exceptions.exists() else ProcessingRun.Status.PASSED
        current.current_stage = "quality_review" if current.exceptions.exists() else "completed"
        current.output_digest = _digest([item["output"]["sha256"] for item in processed])
        current.metrics = {"pages": len(processed), "exceptions": current.exceptions.count(), "missing_pages": missing}
        current.recognition_summary = {"barcodes": sum(bool(item["barcode"]) for item in processed), "qr_codes": sum(bool(item["qr_code"]) for item in processed)}
        current.completed_at = timezone.now()
        current.version += 1
        current.save()
        record_event(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="scan_processing.run.completed",
            aggregate="ProcessingRun",
            aggregate_id=current.id,
            payload={"status": current.status, **current.metrics},
        )
        if current.status == ProcessingRun.Status.PASSED and current.script.state == Script.State.SCANNED:
            transition_script(
                tenant_id=tenant_id,
                actor_id=actor_id,
                script_id=current.script_id,
                expected_version=current.script.version,
                to_state=Script.State.VALIDATED,
                location="validated-repository",
                metadata={"processing_run_id": str(current.id), "output_digest": current.output_digest},
            )
    return current


def resolve_exception(*, tenant_id, actor_id, exception_id, expected_version, status, resolution):
    with transaction.atomic():
        item = ScanQualityException.objects.select_for_update().select_related("run__script").filter(id=exception_id, tenant_id=tenant_id).first()
        if not item or item.version != expected_version:
            raise HttpError(409, "Quality exception is missing or stale")
        if status not in EXCEPTION_TRANSITIONS.get(item.status, set()):
            raise HttpError(409, f"Exception transition from {item.status} to {status} is not allowed")
        item.status = status
        item.resolution = resolution.strip()
        item.resolved_by_id = actor_id if status in (ScanQualityException.Status.RESOLVED, ScanQualityException.Status.SKIPPED) else None
        item.version += 1
        item.save()
        run = item.run
        if status == ScanQualityException.Status.RETURNED_SCANNING:
            run.status = ProcessingRun.Status.RETURNED
        elif not run.exceptions.exclude(status__in=[ScanQualityException.Status.RESOLVED, ScanQualityException.Status.SKIPPED]).exists():
            run.status = ProcessingRun.Status.PASSED
            run.current_stage = "completed"
            run.version += 1
            run.save()
            script = run.script
            if script.state == Script.State.SCANNED:
                transition_script(tenant_id=tenant_id, actor_id=actor_id, script_id=script.id, expected_version=script.version, to_state=Script.State.VALIDATED, location="validated-repository", metadata={"processing_run_id": str(run.id)})
        if run.status == ProcessingRun.Status.RETURNED:
            run.version += 1
            run.save()
        record_event(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="scan_processing.exception.transitioned",
            aggregate="ScanQualityException",
            aggregate_id=item.id,
            payload={"status": status, "run_id": str(run.id)},
        )
    return item
