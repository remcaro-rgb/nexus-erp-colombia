"""Registration of the workflow implementations with ``kernel.services``."""

from __future__ import annotations


def register_services() -> None:
    from kernel import services
    from kernel.interfaces.audit import AuditService
    from kernel.interfaces.files import FileStore
    from kernel.interfaces.notifications import NotificationService
    from kernel.interfaces.scheduler import SchedulerService
    from kernel.interfaces.workflow import WorkflowService
    from workflow.approvals.service import DbWorkflowService
    from workflow.attachments.store import LocalFileStore
    from workflow.audit.service import DbAuditService
    from workflow.notifications.service import DbNotificationService
    from workflow.scheduler.service import DbSchedulerService

    for interface, impl in (
        (AuditService, DbAuditService()),
        (WorkflowService, DbWorkflowService()),
        (SchedulerService, DbSchedulerService()),
        (NotificationService, DbNotificationService()),
        (FileStore, LocalFileStore.from_settings()),
    ):
        if services.optional(interface) is None:
            services.register(interface, impl)
