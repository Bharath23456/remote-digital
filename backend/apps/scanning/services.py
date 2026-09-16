from decimal import Decimal

from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from ninja.errors import HttpError

from apps.core.services import record_event
from apps.custody.models import Script
from apps.custody.services import transition_script

from .models import ScanBatch, ScanJob, ScannerDevice, ScannerErrorLog, ScannerMaintenanceAlert


BATCH_TRANSITIONS = {
    ScanBatch.Status.QUEUED: {ScanBatch.Status.ASSIGNED, ScanBatch.Status.FAILED},
    ScanBatch.Status.ASSIGNED: {ScanBatch.Status.SCANNING, ScanBatch.Status.QUEUED, ScanBatch.Status.FAILED},
    ScanBatch.Status.SCANNING: {ScanBatch.Status.PAUSED, ScanBatch.Status.COMPLETED, ScanBatch.Status.FAILED},
    ScanBatch.Status.PAUSED: {ScanBatch.Status.SCANNING, ScanBatch.Status.QUEUED, ScanBatch.Status.FAILED},
}

MAINTENANCE_TRANSITIONS = {
    ScannerMaintenanceAlert.Status.OPEN: {ScannerMaintenanceAlert.Status.ACKNOWLEDGED},
    ScannerMaintenanceAlert.Status.ACKNOWLEDGED: {ScannerMaintenanceAlert.Status.RESOLVED},
}


@transaction.atomic
def register_scanner(*, tenant_id, actor_id, code, name, location, topology, capabilities, firmware_version):
    if topology not in ScannerDevice.Topology.values:
        raise HttpError(422, "Unsupported scanner topology")
    scanner, created = ScannerDevice.objects.update_or_create(
        tenant_id=tenant_id,
        code=code.strip().upper(),
        defaults={
            "name": name.strip(),
            "location": location.strip(),
            "topology": topology,
            "capabilities": capabilities,
            "firmware_version": firmware_version.strip(),
            "status": ScannerDevice.Status.ONLINE,
            "last_heartbeat_at": timezone.now(),
        },
    )
    if not created:
        scanner.version += 1
        scanner.save(update_fields=["version", "updated_at"])
    record_event(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="scanning.scanner.registered" if created else "scanning.scanner.updated",
        aggregate="ScannerDevice",
        aggregate_id=scanner.id,
        payload={"code": scanner.code, "topology": scanner.topology},
    )
    return scanner


@transaction.atomic
def record_heartbeat(*, tenant_id, actor_id, scanner_id, expected_version, status, pages_per_minute, pages_scanned_today, failure_rate, telemetry):
    scanner = ScannerDevice.objects.select_for_update().filter(id=scanner_id, tenant_id=tenant_id).first()
    if not scanner or scanner.version != expected_version:
        raise HttpError(409, "Scanner is missing or stale")
    if status not in ScannerDevice.Status.values:
        raise HttpError(422, "Unsupported scanner status")
    scanner.status = status
    scanner.last_heartbeat_at = timezone.now()
    scanner.pages_per_minute = max(Decimal("0"), Decimal(str(pages_per_minute)))
    scanner.pages_scanned_today = max(0, pages_scanned_today)
    scanner.failure_rate = max(Decimal("0"), min(Decimal("100"), Decimal(str(failure_rate))))
    scanner.version += 1
    scanner.save()
    if scanner.failure_rate >= Decimal("10") or status == ScannerDevice.Status.DEGRADED:
        ScannerMaintenanceAlert.objects.get_or_create(
            tenant_id=tenant_id,
            scanner=scanner,
            status=ScannerMaintenanceAlert.Status.OPEN,
            defaults={"severity": "high", "reason": "Scanner failure rate or health crossed the maintenance threshold"},
        )
    record_event(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="scanning.scanner.heartbeat",
        aggregate="ScannerDevice",
        aggregate_id=scanner.id,
        payload={"status": status, "pages_per_minute": str(scanner.pages_per_minute), "telemetry": telemetry},
    )
    return scanner


@transaction.atomic
def create_batch(*, tenant_id, actor_id, reference, paper, scripts, topology, duplex, adf, priority):
    if not scripts:
        raise HttpError(422, "At least one registered script is required")
    if any(script.tenant_id != tenant_id or script.paper_id != paper.id for script in scripts):
        raise HttpError(422, "Every script must belong to the selected tenant and paper")
    if any(script.state not in (Script.State.REGISTERED, Script.State.SCANNED) for script in scripts):
        raise HttpError(409, "Only registered or re-scan scripts can enter a scan batch")
    batch = ScanBatch.objects.create(
        tenant_id=tenant_id,
        reference=reference.strip().upper(),
        paper=paper,
        requested_topology=topology,
        duplex=duplex,
        adf=adf,
        priority=min(max(priority, 1), 5),
        expected_scripts=len(scripts),
    )
    for script in scripts:
        ScanJob.objects.create(
            tenant_id=tenant_id,
            batch=batch,
            script=script,
            expected_pages=script.page_count,
        )
    record_event(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="scanning.batch.created",
        aggregate="ScanBatch",
        aggregate_id=batch.id,
        payload={"reference": batch.reference, "scripts": len(scripts), "duplex": duplex, "adf": adf},
    )
    return batch


def assign_next_batch(*, tenant_id, actor_id, scanner_id=None):
    with transaction.atomic():
        scanners = ScannerDevice.objects.select_for_update().filter(
            tenant_id=tenant_id,
            status=ScannerDevice.Status.ONLINE,
        )
        if scanner_id:
            scanners = scanners.filter(id=scanner_id)
        scanner = scanners.annotate(active_batches=Count("batches")).order_by("active_batches", "pages_scanned_today").first()
        if not scanner:
            raise HttpError(409, "No healthy scanner is available")
        batch = ScanBatch.objects.select_for_update().filter(
            tenant_id=tenant_id,
            status=ScanBatch.Status.QUEUED,
            requested_topology=scanner.topology,
        ).order_by("priority", "created_at").first()
        if not batch:
            raise HttpError(404, "Scanner queue is empty")
        batch.scanner = scanner
        batch.status = ScanBatch.Status.ASSIGNED
        batch.assigned_at = timezone.now()
        batch.operator_id = actor_id
        batch.version += 1
        batch.save()
        record_event(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="scanning.batch.assigned",
            aggregate="ScanBatch",
            aggregate_id=batch.id,
            payload={"scanner_id": str(scanner.id)},
        )
    return batch


def transition_batch(*, tenant_id, actor_id, batch_id, expected_version, to_status):
    with transaction.atomic():
        batch = ScanBatch.objects.select_for_update().filter(id=batch_id, tenant_id=tenant_id).first()
        if not batch or batch.version != expected_version:
            raise HttpError(409, "Scan batch is missing or stale")
        if to_status not in BATCH_TRANSITIONS.get(batch.status, set()):
            raise HttpError(409, f"Batch transition from {batch.status} to {to_status} is not allowed")
        previous = batch.status
        batch.status = to_status
        if to_status == ScanBatch.Status.SCANNING and not batch.started_at:
            batch.started_at = timezone.now()
        if to_status == ScanBatch.Status.COMPLETED:
            if batch.jobs.exclude(status=ScanJob.Status.COMPLETED).exists():
                raise HttpError(409, "Every scan job must complete before the batch")
            batch.completed_at = timezone.now()
        batch.version += 1
        batch.save()
        record_event(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="scanning.batch.transitioned",
            aggregate="ScanBatch",
            aggregate_id=batch.id,
            payload={"from": previous, "to": to_status},
        )
    return batch


def complete_job(*, tenant_id, actor_id, job_id, expected_version, page_manifest):
    if not page_manifest or any(not item.get("storage_key") or not item.get("sha256") for item in page_manifest):
        raise HttpError(422, "Every scanned page requires storage key and checksum evidence")
    with transaction.atomic():
        job = ScanJob.objects.select_for_update().select_related("batch", "script").filter(id=job_id, tenant_id=tenant_id).first()
        if not job or job.version != expected_version:
            raise HttpError(409, "Scan job is missing or stale")
        if job.status not in (ScanJob.Status.QUEUED, ScanJob.Status.SCANNING, ScanJob.Status.RESCAN):
            raise HttpError(409, "Scan job cannot be completed from its current state")
        job.status = ScanJob.Status.COMPLETED
        job.scanned_pages = len(page_manifest)
        job.source_manifest = page_manifest
        job.completed_at = timezone.now()
        job.version += 1
        job.save()
        ScanBatch.objects.filter(id=job.batch_id).update(completed_scripts=job.batch.jobs.filter(status=ScanJob.Status.COMPLETED).count())
        record_event(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="scanning.job.completed",
            aggregate="ScanJob",
            aggregate_id=job.id,
            payload={"script_id": str(job.script_id), "pages": len(page_manifest)},
        )
        if job.script.state == Script.State.REGISTERED:
            transition_script(
                tenant_id=tenant_id,
                actor_id=actor_id,
                script_id=job.script_id,
                expected_version=job.script.version,
                to_state=Script.State.SCANNED,
                location="scan-processing-queue",
                metadata={"scan_job_id": str(job.id), "pages": len(page_manifest)},
            )
    return job


def fail_job(*, tenant_id, actor_id, job_id, expected_version, code, message, recoverable=True):
    with transaction.atomic():
        job = ScanJob.objects.select_for_update().select_related("batch__scanner").filter(id=job_id, tenant_id=tenant_id).first()
        if not job or job.version != expected_version:
            raise HttpError(409, "Scan job is missing or stale")
        job.status = ScanJob.Status.RESCAN if recoverable else ScanJob.Status.FAILED
        job.error_code = code[:64]
        job.error_message = message.strip()
        job.attempt += 1 if recoverable else 0
        job.version += 1
        job.save()
        if job.batch.scanner_id:
            ScannerErrorLog.objects.create(
                tenant_id=tenant_id,
                scanner=job.batch.scanner,
                batch=job.batch,
                job=job,
                code=job.error_code,
                message=job.error_message,
                recoverable=recoverable,
            )
        record_event(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="scanning.job.rescan_queued" if recoverable else "scanning.job.failed",
            aggregate="ScanJob",
            aggregate_id=job.id,
            payload={"code": code, "attempt": job.attempt},
        )
    return job


def failover_batch(*, tenant_id, actor_id, batch_id, expected_version):
    with transaction.atomic():
        batch = ScanBatch.objects.select_for_update().select_related("scanner").filter(id=batch_id, tenant_id=tenant_id).first()
        if not batch or batch.version != expected_version:
            raise HttpError(409, "Scan batch is missing or stale")
        if not batch.scanner_id:
            raise HttpError(409, "Scan batch has no scanner to fail over")
        previous = batch.scanner
        replacement = ScannerDevice.objects.filter(
            tenant_id=tenant_id,
            topology=batch.requested_topology,
            status=ScannerDevice.Status.ONLINE,
        ).exclude(id=previous.id).order_by("pages_scanned_today").first()
        if not replacement:
            raise HttpError(409, "No failover scanner is available")
        batch.failover_from = previous
        batch.scanner = replacement
        batch.status = ScanBatch.Status.ASSIGNED
        batch.assigned_at = timezone.now()
        batch.version += 1
        batch.save()
        record_event(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="scanning.batch.failed_over",
            aggregate="ScanBatch",
            aggregate_id=batch.id,
            payload={"from": str(previous.id), "to": str(replacement.id)},
        )
    return batch


@transaction.atomic
def transition_maintenance_alert(*, tenant_id, actor_id, alert_id, target):
    alert = ScannerMaintenanceAlert.objects.select_for_update().select_related("scanner").filter(id=alert_id, tenant_id=tenant_id).first()
    if not alert:
        raise HttpError(404, "Maintenance alert not found")
    if target not in MAINTENANCE_TRANSITIONS.get(alert.status, set()):
        raise HttpError(409, f"Maintenance transition from {alert.status} to {target} is not allowed")
    previous = alert.status
    alert.status = target
    if target == ScannerMaintenanceAlert.Status.ACKNOWLEDGED:
        alert.acknowledged_by_id = actor_id
    else:
        if alert.scanner.status in (ScannerDevice.Status.DEGRADED, ScannerDevice.Status.OFFLINE):
            raise HttpError(409, "Scanner must be healthy before resolving maintenance")
        alert.resolved_by_id = actor_id
    alert.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="scanning.maintenance.transitioned", aggregate="ScannerMaintenanceAlert", aggregate_id=alert.id, payload={"from": previous, "to": target, "scanner_id": str(alert.scanner_id)})
    return alert
