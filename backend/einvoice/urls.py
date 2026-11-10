"""Routes under ``/api/v1/einvoice/`` (contract ``contracts/openapi/modules/einvoice.yaml``) and the KPI route."""

from django.urls import path

from einvoice.api import views

urlpatterns = [
    path("documents", views.DocumentList.as_view()),
    path("documents/<uuid:pk>", views.DocumentDetail.as_view()),
    path("documents/<uuid:pk>:retry", views.DocumentRetry.as_view()),
    path("documents/<uuid:pk>:resend-email", views.DocumentResend.as_view()),
    path("documents/<uuid:pk>/events", views.DocumentEvents.as_view()),
    path("documents/<uuid:pk>/xml", views.DocumentXml.as_view()),
    path("numbering-ranges", views.RangeList.as_view()),
    path("numbering-ranges:sync", views.RangeSync.as_view()),
    path("numbering-ranges/<uuid:pk>", views.RangeDetail.as_view()),
    path("contingency", views.ContingencyView.as_view()),
    path("contingency:declare", views.ContingencyDeclare.as_view()),
    path("contingency:end", views.ContingencyEnd.as_view()),
    path("radian/events", views.RadianList.as_view()),
    path("radian/events:emit", views.RadianEmit.as_view()),
    path("radian/events/<uuid:pk>", views.RadianDetail.as_view()),
    path("habilitation/test-sets", views.TestSetList.as_view()),
    path("habilitation/test-sets/<uuid:pk>", views.TestSetDetail.as_view()),
    path("habilitation/test-sets/<uuid:pk>:run", views.TestSetRun.as_view()),
    path("certificates", views.CertificateList.as_view()),
    path("certificates/<uuid:pk>", views.CertificateDetail.as_view()),
    path("settings", views.SettingsView.as_view()),
    path("providers", views.ProviderList.as_view()),
    path("providers:export", views.ProviderExport.as_view()),
    path("providers/<uuid:pk>", views.ProviderDetail.as_view()),
    path("providers/<uuid:pk>:test-connection", views.ProviderTestConnection.as_view()),
]

#: Mounted at ``api/v1/kpis/facturacion`` by ``EinvoiceConfig.EXTRA_URL_MOUNTS``.
kpi_urlpatterns = [path("", views.KpiView.as_view(), name="kpis-facturacion")]
