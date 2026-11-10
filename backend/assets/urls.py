"""Routes of ``/api/v1/assets/…`` (mounted by ``erp.discovery``) and ``/api/v1/kpis/activos`` (EXTRA_URL_MOUNTS)."""

from django.urls import path

from assets.api import views as v

urlpatterns = [
    path("categories", v.CategoryCollection.as_view(), name="assets-categories"),
    path("categories/<uuid:pk>", v.CategoryItem.as_view(), name="assets-category"),
    path("assets:bulk", v.AssetBulk.as_view(), name="assets-bulk"),
    path("assets", v.AssetCollection.as_view(), name="assets-assets"),
    path("assets/<uuid:pk>:activate", v.AssetActivate.as_view(), name="assets-activate"),
    path("assets/<uuid:pk>:transfer", v.AssetTransfer.as_view(), name="assets-transfer"),
    path("assets/<uuid:pk>:dispose", v.AssetDispose.as_view(), name="assets-dispose"),
    path("assets/<uuid:pk>", v.AssetItem.as_view(), name="assets-asset"),
    path("books", v.BookCollection.as_view(), name="assets-books"),
    path("books/<uuid:pk>", v.BookItem.as_view(), name="assets-book"),
    path("depreciation-runs", v.RunCollection.as_view(), name="assets-runs"),
    path("depreciation-runs/<uuid:pk>:preview", v.RunPreview.as_view(), name="assets-run-preview"),
    path("depreciation-runs/<uuid:pk>:post", v.RunPost.as_view(), name="assets-run-post"),
    path("depreciation-runs/<uuid:pk>", v.RunItem.as_view(), name="assets-run"),
    path("revaluations", v.RevaluationCollection.as_view(), name="assets-revaluations"),
    path("revaluations/<uuid:pk>:post", v.RevaluationPost.as_view(), name="assets-revaluation-post"),
    path("revaluations/<uuid:pk>:simulate", v.RevaluationSimulate.as_view(), name="assets-revaluation-simulate"),
    path("revaluations/<uuid:pk>", v.RevaluationItem.as_view(), name="assets-revaluation"),
    path("impairments", v.ImpairmentCollection.as_view(), name="assets-impairments"),
    path("impairments/<uuid:pk>:post", v.ImpairmentPost.as_view(), name="assets-impairment-post"),
    path("impairments/<uuid:pk>:simulate", v.ImpairmentSimulate.as_view(), name="assets-impairment-simulate"),
    path("impairments/<uuid:pk>", v.ImpairmentItem.as_view(), name="assets-impairment"),
    path("physical-counts", v.CountCollection.as_view(), name="assets-counts"),
    path("physical-counts/<uuid:pk>:submit", v.CountSubmit.as_view(), name="assets-count-submit"),
    path("physical-counts/<uuid:pk>:approve", v.CountApprove.as_view(), name="assets-count-approve"),
    path("physical-counts/<uuid:pk>", v.CountItem.as_view(), name="assets-count"),
    path("reports/register", v.RegisterReport.as_view(), name="assets-report-register"),
    path("reports/depreciation-schedule", v.ScheduleReport.as_view(), name="assets-report-schedule"),
    path("reports/movement", v.MovementReport.as_view(), name="assets-report-movement"),
]

#: mounted at ``api/v1/kpis/`` (module-registry ``activos``)
kpi_urlpatterns = [
    path("activos", v.KpiView.as_view(), name="kpis-activos"),
    path("activos/panels/<str:panel>", v.KpiPanelView.as_view(), name="kpis-activos-panel"),
]
