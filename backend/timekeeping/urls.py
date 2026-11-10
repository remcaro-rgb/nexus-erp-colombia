"""Time keeping routes (absolute; ``TimekeepingConfig.URL_MOUNT = ""``)."""

from timekeeping.views import all_routes

app_name = "timekeeping"
urlpatterns = all_routes()
