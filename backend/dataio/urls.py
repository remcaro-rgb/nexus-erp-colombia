"""Routes of ``/api/v1/dataio/…`` (mounted by ``erp.discovery``) and ``/api/v1/kpis/datos`` (EXTRA_URL_MOUNTS)."""

from django.urls import path

from dataio.api import views as v

urlpatterns = [
    path("templates", v.TemplateList.as_view(), name="dataio-templates"),
    path("templates/<str:entity>/download", v.TemplateDownload.as_view(), name="dataio-template-download"),
    path("templates/<str:entity>", v.TemplateItem.as_view(), name="dataio-template"),
    path("imports", v.ImportCollection.as_view(), name="dataio-imports"),
    path("imports/<uuid:pk>:upload", v.ImportUpload.as_view(), name="dataio-import-upload"),
    path("imports/<uuid:pk>:map", v.ImportMap.as_view(), name="dataio-import-map"),
    path("imports/<uuid:pk>:validate", v.ImportValidate.as_view(), name="dataio-import-validate"),
    path("imports/<uuid:pk>:dry-run", v.ImportValidate.as_view(), name="dataio-import-dry-run"),
    path("imports/<uuid:pk>:commit", v.ImportCommit.as_view(), name="dataio-import-commit"),
    path("imports/<uuid:pk>:resume", v.ImportCommit.as_view(), name="dataio-import-resume"),
    path("imports/<uuid:pk>:cancel", v.ImportCancel.as_view(), name="dataio-import-cancel"),
    path("imports/<uuid:pk>/report", v.ImportReport.as_view(), name="dataio-import-report"),
    path("imports/<uuid:pk>", v.ImportItem.as_view(), name="dataio-import"),
    path("exports", v.ExportCollection.as_view(), name="dataio-exports"),
    path("exports/<uuid:pk>:start", v.ExportStart.as_view(), name="dataio-export-start"),
    path("exports/<uuid:pk>/file", v.ExportFile.as_view(), name="dataio-export-file"),
    path("exports/<uuid:pk>", v.ExportItem.as_view(), name="dataio-export"),
    path("mapping-profiles", v.ProfileCollection.as_view(), name="dataio-profiles"),
    path("mapping-profiles/<uuid:pk>", v.ProfileItem.as_view(), name="dataio-profile"),
]

#: mounted at ``api/v1/kpis/`` (module-registry ``datos``)
kpi_urlpatterns = [
    path("datos", v.KpiView.as_view(), name="kpis-datos"),
    path("datos/panels/<str:panel>", v.KpiPanelView.as_view(), name="kpis-datos-panel"),
]
