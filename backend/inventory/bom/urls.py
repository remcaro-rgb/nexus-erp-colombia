"""BOM routes (included by ``inventory.urls`` at ``/api/v1/inventory/``)."""

from django.urls import path

from inventory.bom import views as v

urlpatterns = [
    path("boms", v.BomListView.as_view(), name="inventory-boms"),
    path("boms/<uuid:pk>", v.BomDetailView.as_view(), name="inventory-bom"),
    path("boms/<uuid:pk>/explode", v.BomExplodeView.as_view(), name="inventory-bom-explode"),
    path("boms/<uuid:pk>:<str:action>", v.BomActionView.as_view(), name="inventory-bom-action"),
]
