"""Transformation, portioning-template and repack-rule routes (included by ``inventory.urls``)."""

from django.urls import path

from inventory.transformations import views as v

urlpatterns = [
    path("transformations", v.TransformationListView.as_view(), name="inventory-transformations"),
    path("transformations:export", v.TransformationExportView.as_view(), name="inventory-transformations-export"),
    path("transformations/<uuid:pk>", v.TransformationDetailView.as_view(), name="inventory-transformation"),
    path(
        "transformations/<uuid:pk>:<str:action>",
        v.TransformationActionView.as_view(),
        name="inventory-transformation-action",
    ),
    path("portioning-templates", v.TemplateListView.as_view(), name="inventory-portioning-templates"),
    path("portioning-templates/<uuid:pk>", v.TemplateDetailView.as_view(), name="inventory-portioning-template"),
    path("repack-rules", v.RepackRuleListView.as_view(), name="inventory-repack-rules"),
    path("repack-rules/<uuid:pk>", v.RepackRuleDetailView.as_view(), name="inventory-repack-rule"),
]
