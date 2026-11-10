"""Ledger routes, mounted at ``/api/v1/ledger/`` (CCR P05-1)."""

from django.urls import path, re_path

from ledger import views
from ledger.dimensions import views as dim_views

DIM = r"(?P<slug>cost-centers|business-units|aux-abierto|item-contable)"
UUID = r"(?P<node_id>[0-9a-fA-F-]{36})"

urlpatterns = [
    re_path(rf"^dimensions/{DIM}$", dim_views.DimensionListView.as_view(), name="ledger-dimension-list"),
    re_path(rf"^dimensions/{DIM}/tree$", dim_views.DimensionTreeView.as_view(), name="ledger-dimension-tree"),
    re_path(rf"^dimensions/{DIM}:export$", dim_views.DimensionExportView.as_view(), name="ledger-dimension-export"),
    re_path(rf"^dimensions/{DIM}/{UUID}$", dim_views.DimensionDetailView.as_view(), name="ledger-dimension-detail"),
    re_path(
        rf"^dimensions/{DIM}/{UUID}:(?P<action>archive|unarchive|move)$",
        dim_views.DimensionActionView.as_view(),
        name="ledger-dimension-action",
    ),
    re_path(rf"^dimensions/{DIM}/{UUID}/usage$", dim_views.DimensionUsageView.as_view(), name="ledger-dimension-usage"),
    path("dimension-rules", views.DimensionRulesView.as_view(), name="ledger-dimension-rules"),
    path("dimension-settings", views.DimensionSettingsView.as_view(), name="ledger-dimension-settings"),
    path("journal-entries:simulate", views.JournalEntrySimulateView.as_view(), name="ledger-journal-simulate"),
    path("entries", views.JournalEntryListView.as_view(), name="ledger-entries"),
    path("entries/<uuid:entry_id>", views.JournalEntryDetailView.as_view(), name="ledger-entry-detail"),
    path("entries/<uuid:entry_id>:reverse", views.JournalEntryReverseView.as_view(), name="ledger-entry-reverse"),
    path("periods", views.PeriodListView.as_view(), name="ledger-periods"),
    re_path(
        r"^periods/(?P<period_id>[0-9a-fA-F-]{36}):(?P<action>soft-close|close)$",
        views.PeriodActionView.as_view(),
        name="ledger-period-action",
    ),
    path("accounts", views.AccountListView.as_view(), name="ledger-accounts"),
]
