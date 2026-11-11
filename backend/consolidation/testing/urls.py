"""Test URLConf: the consolidation and intercompany routes (same mounts as ``erp.discovery``)."""

from django.urls import include, path

from consolidation.urls import intercompany_urlpatterns

urlpatterns = [
    path("api/v1/consolidation/", include("consolidation.urls")),
    path("api/v1/intercompany/", include((intercompany_urlpatterns, "intercompany"))),
]
