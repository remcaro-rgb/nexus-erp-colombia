"""Registration of the workflow implementations with ``kernel.services``."""

from __future__ import annotations

from collections.abc import Callable

from kernel import services


def register_services() -> None:
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

    audit: AuditService = DbAuditService()
    _register_default(AuditService, audit)
    workflow: WorkflowService = DbWorkflowService()
    _register_default(WorkflowService, workflow)
    scheduler: SchedulerService = DbSchedulerService()
    _register_default(SchedulerService, scheduler)
    notifications: NotificationService = DbNotificationService()
    _register_default(NotificationService, notifications)
    files: FileStore = LocalFileStore.from_settings()
    _register_default(FileStore, files)


def _register_default[T](interface: Callable[..., T], implementation: T) -> None:
    """Register ``implementation`` unless something (e.g. a test fake) is registered already."""
    if services.optional(interface) is None:
        services.register(interface, implementation)
