"""Routes under ``/api/v1/purchasing/`` (contract ``contracts/openapi/modules/purchasing.yaml``).

P11a serves RFQs, quotes, comparison, award, orders, receipts, vendor invoices, AP open items, aging and
GRNI. P11b plugs its requisition and supplier-portal routes in without editing this file: when
``purchasing.requisitions.urls`` / ``purchasing.portal.urls`` exist, their ``urlpatterns`` are appended.
"""

from __future__ import annotations

import importlib
import importlib.util
from typing import Any

from django.urls import path

from purchasing.api import views

RFQ_ACTIONS = ("send", "close", "reopen", "evaluate", "award", "approve-award")
ORDER_ACTIONS = ("submit", "approve", "send", "close", "cancel")
RECEIPT_ACTIONS = ("validate", "simulate")
INVOICE_ACTIONS = ("match", "post", "simulate")


def _actions(prefix: str, view: Any, actions: tuple[str, ...]) -> list[Any]:
    """One explicit route per contract action (``POST …/{id}:<verb>``)."""
    return [path(f"{prefix}/<uuid:pk>:{a}", view, {"action": a}) for a in actions]


urlpatterns: list[Any] = [
    path("rfqs", views.RfqList.as_view()),
    path("rfqs/<uuid:pk>", views.RfqDetail.as_view()),
    *_actions("rfqs", views.RfqAction.as_view(), RFQ_ACTIONS),
    path("rfqs/<uuid:pk>/comparison", views.RfqComparison.as_view()),
    path("rfqs/<uuid:rfq_id>/quotes", views.QuoteList.as_view()),
    path("rfqs/<uuid:rfq_id>/quotes/<uuid:pk>", views.QuoteDetail.as_view()),
    path("rfqs/<uuid:rfq_id>/quotes/<uuid:pk>/attachments", views.QuoteAttachments.as_view()),
    path("orders", views.OrderList.as_view()),
    path("orders/<uuid:pk>", views.OrderDetail.as_view()),
    *_actions("orders", views.OrderAction.as_view(), ORDER_ACTIONS),
    path("receipts", views.ReceiptList.as_view()),
    path("receipts/<uuid:pk>", views.ReceiptDetail.as_view()),
    *_actions("receipts", views.ReceiptAction.as_view(), RECEIPT_ACTIONS),
    path("vendor-invoices", views.InvoiceList.as_view()),
    path("vendor-invoices/<uuid:pk>", views.InvoiceDetail.as_view()),
    *_actions("vendor-invoices", views.InvoiceAction.as_view(), INVOICE_ACTIONS),
    path("ap/open-items", views.OpenItemList.as_view()),
    path("ap/open-items/<uuid:pk>", views.OpenItemDetail.as_view()),
    path("ap/aging", views.Aging.as_view()),
    path("reports/grni", views.GrniReport.as_view()),
]

for _sub in ("purchasing.requisitions.urls", "purchasing.portal.urls"):
    if importlib.util.find_spec(_sub.rsplit(".", 1)[0]) is not None and importlib.util.find_spec(_sub) is not None:
        urlpatterns += list(importlib.import_module(_sub).urlpatterns)
