"""Routes under ``/api/v1/sales/`` (contract ``contracts/openapi/modules/sales.yaml``, P10a core).

P10b plugs the customer-portal routes in without editing this file: when ``sales.portal.urls`` exists its
``urlpatterns`` (absolute ``api/portal/v1/customer/*`` paths are mounted by P10b's own include) are appended.
"""

from __future__ import annotations

import importlib
import importlib.util
from typing import Any

from django.urls import path

from sales.api import views

ACTIONS = {
    "parties": ("merge",),
    "quotes": ("send", "accept", "to-order", "cancel", "reprice"),
    "orders": ("confirm", "cancel", "to-invoice"),
    "deliveries": ("validate",),
    "invoices": ("post", "simulate", "send", "reprice"),
    "credit-notes": ("post", "simulate"),
    "debit-notes": ("post", "simulate"),
    "receipts": ("post",),
    "write-offs": ("post", "simulate"),
    "returns": ("submit", "approve", "post"),
}

urlpatterns: list[Any] = [
    path("parties:bulk", views.BulkPartiesView.as_view()),
    path("parties/duplicates", views.DuplicatesView.as_view()),
    path("pricing:quote", views.PricingQuoteView.as_view()),
    path("item-prices", views.ItemPricesView.as_view()),
    path("ar/aging", views.AgingView.as_view()),
    path("ar/statements", views.StatementView.as_view()),
]
for _key in views.RESOURCES:
    _coll, _item, _act = views.views_for(_key)
    urlpatterns += [path(_key, _coll), path(f"{_key}/<uuid:pk>", _item)]
    urlpatterns += [path(f"{_key}/<uuid:pk>:{a}", _act, {"action": a}) for a in ACTIONS.get(_key, ())]

if importlib.util.find_spec("sales.portal") is not None and importlib.util.find_spec("sales.portal.urls") is not None:
    urlpatterns += list(importlib.import_module("sales.portal.urls").urlpatterns)
