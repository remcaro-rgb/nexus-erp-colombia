"""F-INV-ISSUE routes (included at ``/api/v1/inventory/`` by the P09a core ``inventory.urls``)."""

from django.urls import path

from inventory.issues import views as v

urlpatterns = [
    path("issue-concepts", v.ConceptListView.as_view(), name="inventory-issue-concepts"),
    path("issue-concepts/<uuid:pk>", v.ConceptDetailView.as_view(), name="inventory-issue-concept"),
    path("issues", v.IssueListView.as_view(), name="inventory-issues"),
    path("issues:export", v.IssueExportView.as_view(), name="inventory-issues-export"),
    path("issues/<uuid:pk>", v.IssueDetailView.as_view(), name="inventory-issue"),
    path("issues/<uuid:pk>/pdf", v.IssuePdfView.as_view(), name="inventory-issue-pdf"),
    path("issues/<uuid:pk>:<str:action>", v.IssueActionView.as_view(), name="inventory-issue-action"),
]
