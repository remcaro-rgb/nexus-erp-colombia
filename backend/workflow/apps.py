"""Django app of the workflow module (P07).

Registers the real implementations of ``WorkflowService``, ``AuditService``,
``SchedulerService``, ``NotificationService`` and ``FileStore`` with
``kernel.services`` (replace=False: the first registration wins, so tests can
keep their fakes by registering them first).
"""

from typing import ClassVar

from django.apps import AppConfig


class WorkflowConfig(AppConfig):
    name = "workflow"
    label = "workflow"
    verbose_name = "Flujos, auditoría y programador"
    default_auto_field = "django.db.models.BigAutoField"
    #: ``erp.discovery`` (CCR P07-1): routes at ``api/v1/workflow/`` plus ``GET /api/v1/kpis/aprobaciones``.
    EXTRA_URL_MOUNTS: ClassVar[dict[str, tuple[str, str]]] = {"api/v1/kpis/": ("workflow.urls:kpi_urlpatterns", "kpis")}

    def ready(self) -> None:
        from workflow import registry

        registry.register_services()
