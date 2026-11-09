"""Root URL configuration. Modules add their routers via CCR to P01."""

from django.urls import path
from drf_spectacular.views import SpectacularAPIView

from kernel import views

urlpatterns = [
    path("api/health/live", views.health_live, name="health-live"),
    path("api/health/ready", views.health_ready, name="health-ready"),
    path("api/v1/system/environment", views.system_environment, name="system-environment"),
    path("api/v1/ui/context", views.UiContextView.as_view(), name="ui-context"),
    path("api/v1/schema", SpectacularAPIView.as_view(), name="schema"),
]
