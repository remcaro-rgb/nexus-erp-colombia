"""Test URLConf: the assets API and its KPI routes (same mounts as ``erp.discovery``)."""

from django.urls import include, path

from assets.urls import kpi_urlpatterns

urlpatterns = [
    path("api/v1/assets/", include("assets.urls")),
    path("api/v1/kpis/", include((kpi_urlpatterns, "kpis-activos"))),
]
