"""Routes under ``/api/v1/treasury/`` (contract ``contracts/openapi/modules/treasury.yaml``).

P16a serves funds, bank accounts, cash boxes, concepts, recibos de caja (``receipts``), comprobantes de egreso
(``disbursements``), payment batches and bank statements / statement-line matching. One explicit route per contract
action (``POST …/{id}:<verb>``); operations not served yet are listed in ``docs/handoffs/P16a.md``.
"""

from __future__ import annotations

from typing import Any

from django.urls import path

from treasury.api import views

VOUCHER_ACTIONS = ("submit", "approve", "reject", "post", "reverse", "simulate")
BATCH_ACTIONS = ("submit", "approve", "generate-file", "mark-sent", "execute")
LINE_ACTIONS = ("match", "unmatch")


def _actions(prefix: str, view: Any, actions: tuple[str, ...]) -> list[Any]:
    return [path(f"{prefix}/<uuid:pk>:{a}", view, {"action": a}) for a in actions]


def _vouchers(prefix: str, views_: tuple[Any, Any, Any]) -> list[Any]:
    listing, detail, action = views_
    return [path(prefix, listing), path(f"{prefix}/<uuid:pk>", detail), *_actions(prefix, action, VOUCHER_ACTIONS)]


urlpatterns: list[Any] = [
    path("funds", views.FundList.as_view()),
    path("funds/<uuid:pk>", views.FundDetail.as_view()),
    path("funds/<uuid:pk>/snapshot", views.FundSnapshot.as_view()),
    path("bank-accounts", views.BankAccountList.as_view()),
    path("bank-accounts/<uuid:pk>", views.BankAccountDetail.as_view()),
    path("cash-boxes", views.CashBoxList.as_view()),
    path("cash-boxes/<uuid:pk>", views.CashBoxDetail.as_view()),
    path("concepts", views.ConceptList.as_view()),
    path("concepts/<uuid:pk>", views.ConceptDetail.as_view()),
    *_vouchers("receipts", views.RECEIPT_VIEWS),
    *_vouchers("disbursements", views.DISBURSEMENT_VIEWS),
    path("payment-batches", views.BatchList.as_view()),
    path("payment-batches/<uuid:pk>", views.BatchDetail.as_view()),
    *_actions("payment-batches", views.BatchAction.as_view(), BATCH_ACTIONS),
    path("statements", views.StatementList.as_view()),
    path("statements:import", views.StatementImport.as_view()),
    path("statements/<uuid:pk>", views.StatementDetail.as_view()),
    path("statement-lines", views.StatementLineList.as_view()),
    path("statement-lines/<uuid:pk>", views.StatementLineDetail.as_view()),
    *_actions("statement-lines", views.StatementLineAction.as_view(), LINE_ACTIONS),
]

#: mounted at ``api/v1/kpis/`` by ``TreasuryConfig.EXTRA_URL_MOUNTS`` (module-registry ``tesoreria``)
kpi_urlpatterns: list[Any] = [
    path("tesoreria", views.KpiView.as_view(), name="kpis-tesoreria"),
    path("tesoreria/panels/<str:panel>", views.KpiPanelView.as_view(), name="kpis-tesoreria-panel"),
]
