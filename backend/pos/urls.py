"""Routes under ``/api/v1/pos/`` (contract ``contracts/openapi/modules/pos.yaml``) and ``/api/v1/kpis/pos``."""

from __future__ import annotations

from typing import Any

from django.urls import path

from pos.api import views

ACTIONS = {
    "terminals": ("register",),
    "sessions": ("close",),
    "tickets": ("to-invoice",),
    "returns": ("post", "simulate"),
}

urlpatterns: list[Any] = [
    path("sessions:open", views.SessionOpenView.as_view()),
    path("tickets:checkout", views.CheckoutView.as_view()),
    path("tickets:preview", views.PreviewView.as_view()),
    path("reports/z", views.ZReportView.as_view()),
    path("catalog-snapshot", views.CatalogSnapshotView.as_view()),
]
for _key in views.RESOURCES:
    _coll, _item, _act = views.views_for(_key)
    urlpatterns += [path(_key, _coll), path(f"{_key}/<uuid:pk>", _item)]
    urlpatterns += [path(f"{_key}/<uuid:pk>:{a}", _act, {"action": a}) for a in ACTIONS.get(_key, ())]

#: Mounted at ``api/v1/kpis/`` by ``PosAppConfig.EXTRA_URL_MOUNTS`` (registry dashboard ``pos``).
kpi_urlpatterns = [
    path("pos", views.KpiView.as_view(), name="kpis-pos"),
    path("pos/panels/<slug:panel_id>", views.KpiPanelView.as_view(), name="kpis-pos-panel"),
]
