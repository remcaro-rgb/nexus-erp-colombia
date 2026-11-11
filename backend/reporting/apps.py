"""Django app of the reporting module (P17a core: semantic layer, query engine, saved reports, KPIs).

Routes at ``api/v1/reporting/`` (``erp.discovery``) plus ``GET /api/v1/kpis/reportes[/panels/ejecuciones_diarias]``.
``ready`` validates the semantic models (an invalid YAML fails start-up and CI), registers
``kernel.interfaces.ReportQueryService`` and the P07 ``report_delivery`` scheduled-job handler.
"""

from typing import ClassVar

from django.apps import AppConfig


class ReportingConfig(AppConfig):
    name = "reporting"
    label = "reporting"
    verbose_name = "Informes y capa semántica"
    default_auto_field = "django.db.models.BigAutoField"
    EXTRA_URL_MOUNTS: ClassVar[dict[str, tuple[str, str]]] = {
        "api/v1/kpis/": ("reporting.urls:kpi_urlpatterns", "kpis-reportes")
    }

    def ready(self) -> None:
        from kernel import services
        from kernel.interfaces.reporting import ReportQueryService
        from reporting.engine import SERVICE
        from reporting.reports import JOB_TYPE, scheduled_handler
        from reporting.semantic.registry import entities

        entities()
        if services.optional(ReportQueryService) is None:
            svc: ReportQueryService = SERVICE
            services.register(ReportQueryService, svc)
        try:
            from workflow.scheduler.registry import register_job_type
        except ImportError:  # P07 absent: scheduling fails closed (SchedulerService unavailable)
            return
        register_job_type(JOB_TYPE, "reporting.report.read", "Envío de informes", scheduled_handler, replace=True)
