"""Customer-portal routes (absolute ``api/portal/v1/customer/*``; contract ``portal.yaml``) and the back-office routes
of the portal under ``/api/v1/sales/``.

``sales.urls`` (P10a) appends this module's ``urlpatterns`` under ``/api/v1/sales/`` — the portal routes cannot
live there. They are exported as :data:`portal_patterns` and mounted at ``api/portal/v1/customer/`` through
``erp.discovery``'s ``EXTRA_URL_MOUNTS`` of the ``sales`` app: importing this module (which ``sales.urls`` does
while the root URL configuration mounts the sales app, before discovery reads ``EXTRA_URL_MOUNTS``) registers the
mount on the app config. CCR P10b-1 asks P10a to declare it in ``SalesConfig`` instead; then this is a no-op.
"""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.urls import path

from sales.portal import views

PREFIX = "api/portal/v1/customer/"
MOUNT = ("sales.portal.urls:portal_patterns", "sales-portal")

#: internal routes under /api/v1/sales/ (back office of the portal; CCR P10b-2)
urlpatterns: list[Any] = [
    path("portal-requests", views.InternalRequestsView.as_view()),
    *[
        path(f"portal-requests/<uuid:pk>:{a}", views.InternalRequestActionView.as_view(), {"action": a})
        for a in ("quote", "approve", "reject")
    ],
    path("portal-requests/<uuid:pk>", views.InternalRequestsView.as_view()),
    path("payment-intents", views.InternalIntentsView.as_view()),
    path("payment-intents/<uuid:pk>", views.InternalIntentsView.as_view()),
]

portal_patterns: list[Any] = [
    path("quotes", views.QuotesView.as_view()),
    path("quotes/<str:pk>:accept", views.QuoteAcceptView.as_view()),
    path("quotes/<str:pk>", views.QuotesView.as_view()),
    path("orders", views.OrdersView.as_view()),
    path("orders/<str:pk>", views.OrdersView.as_view()),
    path("invoices", views.InvoicesView.as_view()),
    path("invoices/<str:pk>/pdf", views.InvoicePdfView.as_view()),
    path("invoices/<str:pk>/xml", views.InvoiceXmlView.as_view()),
    path("invoices/<str:pk>", views.InvoicesView.as_view()),
    path("credit-notes", views.CreditNotesView.as_view()),
    path("credit-notes/<str:pk>/pdf", views.CreditNotePdfView.as_view()),
    path("credit-notes/<str:pk>/xml", views.CreditNoteXmlView.as_view()),
    path("credit-notes/<str:pk>", views.CreditNotesView.as_view()),
    path("receipts", views.ReceiptsView.as_view()),
    path("receipts/<str:pk>", views.ReceiptsView.as_view()),
    path("open-items", views.OpenItemsView.as_view()),
    path("statement", views.StatementView.as_view()),
    path("payment-intents", views.PaymentIntentsView.as_view()),
    path("payment-intents/<str:pk>:simulate", views.PaymentIntentSimulateView.as_view()),
    path("payment-intents/<str:pk>", views.PaymentIntentsView.as_view()),
    path("requests", views.RequestsView.as_view()),
    path("requests/<str:pk>", views.RequestsView.as_view()),
    path("profile", views.ProfileView.as_view()),
    path("kpis", views.KpisView.as_view()),
]


def register_mount() -> None:
    cfg = apps.get_app_config("sales")
    mounts: dict[str, tuple[str, str]] = dict(getattr(cfg, "EXTRA_URL_MOUNTS", {}) or {})
    if PREFIX not in mounts:
        mounts[PREFIX] = MOUNT
        cfg.EXTRA_URL_MOUNTS = mounts  # type: ignore[attr-defined]  # discovery hook read by erp.discovery


register_mount()
