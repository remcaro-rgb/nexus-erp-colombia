"""``manage.py audit_verify [--tenant UUID]`` — recompute the audit hash chain(s); non-zero on tampering."""

from __future__ import annotations

import uuid
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser


class Command(BaseCommand):
    help = "Verify the tamper-evident audit hash chain of one or every tenant."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--tenant", type=uuid.UUID, default=None)

    def handle(self, *args: Any, **options: Any) -> None:
        from workflow.audit.service import DbAuditService
        from workflow.models import AuditEvent

        tenants = [options["tenant"]] if options["tenant"] else list(
            AuditEvent.objects.values_list("tenant_id", flat=True).distinct()
        )
        failed = []
        for tenant in tenants:
            report = DbAuditService().verify(tenant)
            status = "OK" if report.ok else f"TAMPERED at seq {report.first_bad_seq} ({report.reason})"
            self.stdout.write(f"{tenant}: {status} — {report.checked} events")
            if not report.ok:
                failed.append(str(tenant))
        if failed:
            raise CommandError(f"audit chain verification failed for {len(failed)} tenant(s)")
