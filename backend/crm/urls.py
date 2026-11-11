"""Routes under ``/api/v1/crm/`` (contract ``contracts/openapi/modules/crm.yaml``)."""

from __future__ import annotations

from typing import Any

from django.urls import path

from crm.api import views
from sales.api.views import views_for

urlpatterns: list[Any] = [
    path("leads:bulk", views.BulkLeadsView.as_view()),
    path("forecast", views.ForecastView.as_view()),
]
for _key in views.RESOURCES:
    _coll, _item, _act = views_for(_key, views.CrmResources)
    urlpatterns += [path(_key, _coll), path(f"{_key}/<uuid:pk>", _item)]
    urlpatterns += [path(f"{_key}/<uuid:pk>:{a}", _act, {"action": a}) for a in views.ACTIONS.get(_key, ())]
