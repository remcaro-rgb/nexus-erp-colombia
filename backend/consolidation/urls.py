"""Routes of ``/api/v1/consolidation/…`` (mounted by ``erp.discovery``) and ``/api/v1/intercompany/…``
(``EXTRA_URL_MOUNTS``); exactly the operations of consolidation.yaml and intercompany.yaml 1.0.0."""

from django.urls import path

from consolidation.api import views as v

urlpatterns = [
    path("groups", v.GroupCollection.as_view(), name="consolidation-groups"),
    path("groups/<uuid:pk>", v.GroupItem.as_view(), name="consolidation-group"),
    path("runs", v.RunCollection.as_view(), name="consolidation-runs"),
    path("runs/<uuid:pk>:preview", v.RunPreview.as_view(), name="consolidation-run-preview"),
    path("runs/<uuid:pk>:post", v.RunPost.as_view(), name="consolidation-run-post"),
    path("runs/<uuid:pk>", v.RunItem.as_view(), name="consolidation-run"),
    path("reports/balance", v.BalanceReport.as_view(), name="consolidation-report-balance"),
    path("reports/income-statement", v.IncomeStatementReport.as_view(), name="consolidation-report-income"),
    path("reports/eliminations", v.EliminationsReport.as_view(), name="consolidation-report-eliminations"),
]

#: mounted at ``api/v1/intercompany/``
intercompany_urlpatterns = [
    path("agreements", v.AgreementCollection.as_view(), name="intercompany-agreements"),
    path("agreements/<uuid:pk>", v.AgreementItem.as_view(), name="intercompany-agreement"),
    path("reconciliation", v.ReconciliationReport.as_view(), name="intercompany-reconciliation"),
]
