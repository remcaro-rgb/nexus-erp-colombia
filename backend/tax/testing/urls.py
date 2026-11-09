"""URLconf for tests: the root URLs plus the tax routes (CCR P06-1 asks P01 to include them)."""

from django.urls import include, path

from erp.urls import urlpatterns as root_patterns

urlpatterns = [*root_patterns, path("api/v1/tax/", include("tax.urls"))]
