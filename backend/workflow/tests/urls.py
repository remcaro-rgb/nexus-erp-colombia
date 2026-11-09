"""Test URLConf: the workflow routes mounted as ``erp.urls`` will mount them (CCR P07-1)."""

from django.urls import include, path

from workflow.urls import kpi_urlpatterns

urlpatterns = [
    path("api/v1/workflow/", include("workflow.urls")),
    path("api/v1/kpis/", include((kpi_urlpatterns, "kpis"))),
]
