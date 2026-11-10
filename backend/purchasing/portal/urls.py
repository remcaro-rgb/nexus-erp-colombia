"""Supplier-portal routes (absolute ``api/portal/v1/supplier/*``; contract ``portal.yaml``).

``purchasing.urls`` (P11a) appends this module's ``urlpatterns`` under ``/api/v1/purchasing/`` — the portal
routes cannot live there. They are exported as :data:`portal_patterns` and mounted at ``api/portal/v1/supplier/``
through ``erp.discovery``'s ``EXTRA_URL_MOUNTS`` of the ``purchasing`` app: importing this module (which
``purchasing.urls`` does while the root URL configuration mounts the purchasing app, before discovery reads
``EXTRA_URL_MOUNTS``) registers the mount on the app config. CCR P11b-1 asks P11a to declare it in
``PurchasingConfig`` instead; then the registration below is a no-op.
"""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.urls import path

from purchasing.portal import views

PREFIX = "api/portal/v1/supplier/"
MOUNT = ("purchasing.portal.urls:portal_patterns", "purchasing-portal")

#: internal routes under /api/v1/purchasing/ (none in the frozen contract yet; CCR P11b-2)
urlpatterns: list[Any] = []

portal_patterns: list[Any] = [
    path("profile", views.ProfileView.as_view()),
    path("profile/change-requests", views.ProfileChangeView.as_view()),
    path("documents", views.DocumentsView.as_view()),
    path("documents/<str:pk>", views.DocumentsView.as_view()),
    path("rfqs", views.RfqsView.as_view()),
    path("rfqs/<str:rfq_id>/quotes", views.QuotesView.as_view()),
    path("rfqs/<str:rfq_id>/quotes/<str:pk>", views.QuotesView.as_view()),
    path("rfqs/<str:pk>", views.RfqsView.as_view()),
    path("purchase-orders", views.OrdersView.as_view()),
    path("purchase-orders/<str:pk>:acknowledge", views.OrderAcknowledgeView.as_view()),
    path("purchase-orders/<str:pk>", views.OrdersView.as_view()),
    path("shipments", views.ShipmentsView.as_view()),
    path("shipments/<str:pk>", views.ShipmentsView.as_view()),
    path("invoices", views.InvoicesView.as_view()),
    path("invoices/<str:pk>", views.InvoicesView.as_view()),
    path("payments", views.PaymentsView.as_view()),
    path("payments/<str:pk>", views.PaymentsView.as_view()),
    path("kpis", views.KpisView.as_view()),
]


def register_mount() -> None:
    cfg = apps.get_app_config("purchasing")
    mounts: dict[str, tuple[str, str]] = dict(getattr(cfg, "EXTRA_URL_MOUNTS", {}) or {})
    if PREFIX not in mounts:
        mounts[PREFIX] = MOUNT
        cfg.EXTRA_URL_MOUNTS = mounts  # type: ignore[attr-defined]  # discovery hook read by erp.discovery


register_mount()
