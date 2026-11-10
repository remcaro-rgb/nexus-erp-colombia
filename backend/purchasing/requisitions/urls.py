"""F-INV-REQ routes, appended by ``purchasing.urls`` (P11a) under ``/api/v1/purchasing/`` — one explicit route per
contract action. Importing this module also installs the P11b extension (models + migrations, see
:mod:`purchasing.requisitions.extension`)."""

from __future__ import annotations

from typing import Any

from django.urls import path

from purchasing.requisitions import views

IR_ACTIONS = ("submit", "approve", "reject", "cancel", "source")
PR_ACTIONS = ("submit", "approve", "reject", "to-rfq", "to-po")


def _actions(prefix: str, view: Any, actions: tuple[str, ...]) -> list[Any]:
    return [path(f"{prefix}/<uuid:pk>:{a}", view, {"action": a}) for a in actions]


urlpatterns: list[Any] = [
    path("internal-requisitions", views.InternalRequisitionList.as_view()),
    path("internal-requisitions/<uuid:pk>", views.InternalRequisitionDetail.as_view()),
    *_actions("internal-requisitions", views.InternalRequisitionAction.as_view(), IR_ACTIONS),
    path("consolidations", views.ConsolidationList.as_view()),
    path("consolidations:run", views.ConsolidationRun.as_view()),
    path("consolidations/<uuid:pk>", views.ConsolidationDetail.as_view()),
    path("requisitions", views.PurchaseRequisitionList.as_view()),
    path("requisitions/<uuid:pk>", views.PurchaseRequisitionDetail.as_view()),
    *_actions("requisitions", views.PurchaseRequisitionAction.as_view(), PR_ACTIONS),
]
