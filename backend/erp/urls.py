"""Root URL configuration.

Kernel routes are listed here; every other installed first-party app is mounted by
:func:`erp.discovery.app_url_patterns` (``api/v1/<label>/`` unless its AppConfig declares
``URL_MOUNT`` / ``EXTRA_URL_MOUNTS``), so a new module needs no edit of this file.
"""

from django.apps import apps
from django.conf import settings
from django.urls import path
from drf_spectacular.views import SpectacularAPIView

from erp.discovery import app_url_patterns
from kernel import views

urlpatterns = [
    path("api/health/live", views.health_live, name="health-live"),
    path("api/health/ready", views.health_ready, name="health-ready"),
    path("api/v1/system/environment", views.system_environment, name="system-environment"),
    path("api/v1/ui/context", views.UiContextView.as_view(), name="ui-context"),
    path("api/v1/schema", SpectacularAPIView.as_view(), name="schema"),
    *app_url_patterns(apps.get_app_configs(), settings.ERP_LOCAL_APPS),
]
