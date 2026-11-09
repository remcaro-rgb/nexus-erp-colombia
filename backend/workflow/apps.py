"""Django app of the workflow module (P07).

Registers the real implementations of ``WorkflowService``, ``AuditService``,
``SchedulerService``, ``NotificationService`` and ``FileStore`` with
``kernel.services`` (replace=False: the first registration wins, so tests can
keep their fakes by registering them first).
"""

from django.apps import AppConfig


class WorkflowConfig(AppConfig):
    name = "workflow"
    label = "workflow"
    verbose_name = "Flujos, auditoría y programador"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from workflow import registry

        registry.register_services()
