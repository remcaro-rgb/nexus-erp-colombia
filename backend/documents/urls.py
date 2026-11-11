"""Routes under ``/api/v1/documents/`` (contract ``contracts/openapi/modules/documents.yaml``)."""

from __future__ import annotations

from typing import Any

from django.urls import path

from documents import views

urlpatterns: list[Any] = [
    path("templates", views.TemplateList.as_view()),
    path("templates/<uuid:pk>", views.TemplateDetail.as_view()),
    path("templates/<uuid:pk>:preview", views.TemplatePreview.as_view()),
    path("render:render-batch", views.RenderBatchView.as_view()),
    path("render", views.Render.as_view()),
]
