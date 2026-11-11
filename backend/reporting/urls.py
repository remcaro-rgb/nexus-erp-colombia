"""Routes under ``/api/v1/reporting/`` (contract ``contracts/openapi/modules/reporting.yaml``).

P17a serves the semantic model, ``/query`` (+ ``:explain``) and saved reports. The standard reports
(``standard/...``, P17b) and exógena / certificates (``exogena/...``, ``certificates:generate``, P17c) are mounted
from their owners' packages when present (``reporting/standard/urls.py``, ``reporting/exogena/urls.py``) — the
frozen extension point of :mod:`reporting.interfaces`; nothing is routed for them before they exist.
"""

from __future__ import annotations

import importlib
import importlib.util
from typing import Any

from django.urls import path

from reporting import views

REPORT_ACTIONS = ("run", "schedule", "publish", "archive")

urlpatterns: list[Any] = [
    path("semantic-model", views.SemanticModel.as_view()),
    path("query:explain", views.QueryExplain.as_view()),
    path("query", views.Query.as_view()),
    path("reports", views.ReportList.as_view()),
    path("reports/<uuid:pk>", views.ReportDetail.as_view()),
    *[path(f"reports/<uuid:pk>:{a}", views.ReportAction.as_view(), {"action": a}) for a in REPORT_ACTIONS],
]

for _part in ("reporting.standard.urls", "reporting.exogena.urls"):
    if importlib.util.find_spec(_part.rsplit(".", 1)[0]) is not None and importlib.util.find_spec(_part) is not None:
        urlpatterns += list(importlib.import_module(_part).urlpatterns)

#: mounted at ``api/v1/kpis/`` by ``ReportingConfig.EXTRA_URL_MOUNTS`` (module-registry ``reportes``)
kpi_urlpatterns: list[Any] = [
    path("reportes", views.KpiView.as_view(), name="kpis-reportes"),
    path("reportes/panels/<str:panel>", views.KpiPanelView.as_view(), name="kpis-reportes-panel"),
]
