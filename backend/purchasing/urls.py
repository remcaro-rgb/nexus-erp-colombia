"""Routes under ``/api/v1/purchasing/`` (contract ``contracts/openapi/modules/purchasing.yaml``).

P11a serves RFQs, quotes, comparison, award, orders, receipts, vendor invoices, AP open items, aging and
GRNI. P11b plugs its requisition and supplier-portal routes in without editing this file: when
``purchasing.requisitions.urls`` / ``purchasing.portal.urls`` exist, their ``urlpatterns`` are appended.
"""

from __future__ import annotations

import importlib
import importlib.util
from typing import Any

from django.urls import path, re_path

from purchasing.api import views

UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"

urlpatterns: list[Any] = [
    path("rfqs", views.RfqList.as_view()),
    path("rfqs/<uuid:pk>", views.RfqDetail.as_view()),
    re_path(
        rf"^rfqs/(?P<pk>{UUID}):(?P<action>send|close|reopen|evaluate|award|approve-award)$", views.RfqAction.as_view()
    ),
    path("rfqs/<uuid:pk>/comparison", views.RfqComparison.as_view()),
    path("rfqs/<uuid:rfq_id>/quotes", views.QuoteList.as_view()),
    path("rfqs/<uuid:rfq_id>/quotes/<uuid:pk>", views.QuoteDetail.as_view()),
    path("rfqs/<uuid:rfq_id>/quotes/<uuid:pk>/attachments", views.QuoteAttachments.as_view()),
    path("orders", views.OrderList.as_view()),
    path("orders/<uuid:pk>", views.OrderDetail.as_view()),
    re_path(rf"^orders/(?P<pk>{UUID}):(?P<action>submit|approve|send|close|cancel)$", views.OrderAction.as_view()),
    path("receipts", views.ReceiptList.as_view()),
    path("receipts/<uuid:pk>", views.ReceiptDetail.as_view()),
    re_path(rf"^receipts/(?P<pk>{UUID}):(?P<action>validate|simulate)$", views.ReceiptAction.as_view()),
    path("vendor-invoices", views.InvoiceList.as_view()),
    path("vendor-invoices/<uuid:pk>", views.InvoiceDetail.as_view()),
    re_path(rf"^vendor-invoices/(?P<pk>{UUID}):(?P<action>match|post|simulate)$", views.InvoiceAction.as_view()),
    path("ap/open-items", views.OpenItemList.as_view()),
    path("ap/open-items/<uuid:pk>", views.OpenItemDetail.as_view()),
    path("ap/aging", views.Aging.as_view()),
    path("reports/grni", views.GrniReport.as_view()),
]

for _sub in ("purchasing.requisitions.urls", "purchasing.portal.urls"):
    if importlib.util.find_spec(_sub.rsplit(".", 1)[0]) is not None and importlib.util.find_spec(_sub) is not None:
        urlpatterns += list(importlib.import_module(_sub).urlpatterns)
