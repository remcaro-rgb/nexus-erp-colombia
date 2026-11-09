"""URL patterns of the tax API (contract ``contracts/openapi/modules/tax.yaml``)."""

from django.urls import path

from tax.api import views
from tax.api.resources import views_for

urlpatterns = [
    path("parameters", views.ParameterList.as_view()),
    path("parameters:propose", views.ParameterPropose.as_view()),
    path("parameters/<uuid:pk>", views.ParameterDetail.as_view()),
    path("parameters/<uuid:pk>:propose", views.ParameterProposeChange.as_view()),
    path("parameters/<uuid:pk>:approve", views.ParameterApprove.as_view()),
    path("parameters/<uuid:pk>/impact", views.ParameterImpact.as_view()),
    path("sources", views.SourceList.as_view()),
    path("sources/<uuid:pk>", views.SourceDetail.as_view()),
    path("compute", views.Compute.as_view()),
    path("localization/municipalities", views.MunicipalityList.as_view()),
    path("localization/municipalities/<uuid:pk>", views.MunicipalityDetail.as_view()),
    path("localization/holidays", views.HolidayList.as_view()),
    path("localization/holidays/<uuid:pk>", views.HolidayDetail.as_view()),
    path("localization/nit:validate", views.NitValidate.as_view()),
    path("exogena/format-packs", views.ExogenaPackList.as_view()),
    path("exogena/format-packs:import", views.ExogenaPackImport.as_view()),
    path("exogena/format-packs/<uuid:pk>", views.ExogenaPackDetail.as_view()),
]

for _name in ("tax-codes", "withholding-concepts", "ica-tariffs", "party-tax-profiles", "calendars"):
    _list, _detail = views_for(_name)
    urlpatterns += [path(_name, _list), path(f"{_name}/<uuid:pk>", _detail)]
