"""Inventory routes, mounted at ``/api/v1/inventory/`` (erp.discovery).

Extension parts (P09b/P09c) add ``inventory/<part>/urls.py`` with a ``urlpatterns`` list; it is included here
when present, so they need no edit of this file.
"""

import importlib
import importlib.util

from django.urls import include, path

from inventory.api import views as v

urlpatterns = [
    path("uoms", v.UomListView.as_view(), name="inventory-uoms"),
    path("uoms/<uuid:pk>", v.UomDetailView.as_view(), name="inventory-uom"),
    path("items", v.ItemListView.as_view(), name="inventory-items"),
    path("items/<uuid:pk>", v.ItemDetailView.as_view(), name="inventory-item"),
    path("items/<uuid:item_id>/presentations", v.PresentationListView.as_view(), name="inventory-presentations"),
    path(
        "items/<uuid:item_id>/presentations/<uuid:pk>",
        v.PresentationDetailView.as_view(),
        name="inventory-presentation",
    ),
    path(
        "items/<uuid:item_id>/presentations/<uuid:pk>:<str:action>",
        v.PresentationActionView.as_view(),
        name="inventory-presentation-action",
    ),
    path("warehouses", v.WarehouseListView.as_view(), name="inventory-warehouses"),
    path("warehouses/<uuid:pk>", v.WarehouseDetailView.as_view(), name="inventory-warehouse"),
    path("warehouses/<uuid:warehouse_id>/locations", v.LocationListView.as_view(), name="inventory-locations"),
    path("warehouses/<uuid:warehouse_id>/locations/tree", v.LocationTreeView.as_view(), name="inventory-location-tree"),
    path(
        "warehouses/<uuid:warehouse_id>/locations/<uuid:pk>", v.LocationDetailView.as_view(), name="inventory-location"
    ),
    path(
        "warehouses/<uuid:warehouse_id>/locations/<uuid:pk>:archive",
        v.LocationArchiveView.as_view(),
        name="inventory-location-archive",
    ),
    path(
        "warehouses/<uuid:warehouse_id>/putaway-rules", v.PutawayRuleListView.as_view(), name="inventory-putaway-rules"
    ),
    path("locations", v.LocationSearchView.as_view(), name="inventory-location-search"),
    path("lots", v.LotListView.as_view(), name="inventory-lots"),
    path("moves", v.MoveListView.as_view(), name="inventory-moves"),
    path("moves:internal", v.MoveInternalView.as_view(), name="inventory-moves-internal"),
    path("moves/<uuid:pk>", v.MoveDetailView.as_view(), name="inventory-move"),
    path("quants", v.QuantListView.as_view(), name="inventory-quants"),
    path("quants/<uuid:pk>", v.QuantDetailView.as_view(), name="inventory-quant"),
    path("availability", v.AvailabilityView.as_view(), name="inventory-availability"),
    path("reports/availability", v.AvailabilityView.as_view(), name="inventory-report-availability"),
    path("transfers", v.TransferListView.as_view(), name="inventory-transfers"),
    path("transfers/<uuid:pk>", v.TransferDetailView.as_view(), name="inventory-transfer"),
    path(
        "transfers/<uuid:pk>:<str:action>",
        v.TransferActionView.as_view(),
        name="inventory-transfer-action",
    ),
]

for _part in ("issues", "barcodes", "bom", "transformations"):
    if importlib.util.find_spec(f"inventory.{_part}") is not None and importlib.util.find_spec(
        f"inventory.{_part}.urls"
    ):
        urlpatterns.append(path("", include(f"inventory.{_part}.urls")))
