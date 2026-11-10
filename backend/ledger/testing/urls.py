"""Root URLconf = erp.urls + the ledger routes (until CCR P05-1 wires ``ledger.urls``)."""

from django.urls import include, path

from erp.urls import urlpatterns as erp_urlpatterns

urlpatterns = [*erp_urlpatterns, path("api/v1/ledger/", include("ledger.urls"))]
