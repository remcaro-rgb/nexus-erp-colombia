"""F-INV-BARCODE routes (included at ``/api/v1/inventory/`` by the P09a core ``inventory.urls``)."""

from django.urls import path

from inventory.barcodes import views as v

urlpatterns = [
    path("barcodes:generate", v.BarcodeGenerateView.as_view(), name="inventory-barcodes-generate"),
    path("barcodes:validate", v.BarcodeValidateView.as_view(), name="inventory-barcodes-validate"),
    path("barcodes/lookup", v.BarcodeLookupView.as_view(), name="inventory-barcodes-lookup"),
    path("label-templates", v.LabelTemplateListView.as_view(), name="inventory-label-templates"),
    path("label-templates/<uuid:pk>", v.LabelTemplateDetailView.as_view(), name="inventory-label-template"),
    path("label-print-jobs", v.LabelPrintJobListView.as_view(), name="inventory-label-print-jobs"),
    path("label-print-jobs/<uuid:pk>", v.LabelPrintJobDetailView.as_view(), name="inventory-label-print-job"),
    path("label-print-jobs/<uuid:pk>/pdf", v.LabelPrintJobPdfView.as_view(), name="inventory-label-print-job-pdf"),
    path(
        "label-print-jobs/<uuid:pk>/preview.png",
        v.LabelPrintJobPreviewView.as_view(),
        name="inventory-label-print-job-preview",
    ),
]
