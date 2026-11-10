"""HR routes (absolute; ``HrConfig.URL_MOUNT = ""``): internal ``api/v1/hr/*`` and the employee portal."""

from django.urls import path

from hr.api.views import internal_routes
from hr.portal import views as portal

P = "api/portal/v1/employee"

urlpatterns = [
    *internal_routes(),
    path(f"{P}/payslips", portal.PayslipsView.as_view()),
    path(f"{P}/payslips/<str:pk>/pdf", portal.PayslipPdfView.as_view()),
    path(f"{P}/payslips/<str:pk>", portal.PayslipsView.as_view()),
    path(f"{P}/certificates", portal.CertificatesView.as_view()),
    path(f"{P}/certificates/<str:pk>/pdf", portal.CertificatePdfView.as_view()),
    path(f"{P}/certificates/<str:pk>", portal.CertificatesView.as_view()),
    path(f"{P}/vacations", portal.VacationsView.as_view()),
    path(f"{P}/vacations/<str:pk>", portal.VacationsView.as_view()),
    path(f"{P}/data-updates", portal.DataUpdatesView.as_view()),
    path(f"{P}/data-updates/<str:pk>", portal.DataUpdatesView.as_view()),
    path(f"{P}/profile", portal.ProfileView.as_view()),
    path(f"{P}/kpis", portal.KpisView.as_view()),
]
