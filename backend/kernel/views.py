"""Health, environment and UI-context endpoints (D-P01, UI task matrix P01).

``/api/health/live``, ``/api/health/ready`` and ``/api/v1/system/environment``
are the only anonymous endpoints; they return no secrets. ``/api/v1/ui/context``
requires authentication (deny by default).
"""

from __future__ import annotations

import subprocess
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from kernel import ids, tenancy
from kernel.errors import TenancyNotBound
from kernel.interfaces.periods import PeriodService
from kernel.services import optional


@require_GET
def health_live(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


def _check_db() -> bool:
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
            return bool(cur.fetchone() == (1,))
    except Exception:  # noqa: BLE001 - readiness reports, never raises
        return False


def _check_cache() -> bool:
    key = f"erp:health:{ids.new_id()}"
    try:
        cache.set(key, "1", timeout=5)
        ok = bool(cache.get(key) == "1")
        cache.delete(key)
        return ok
    except Exception:  # noqa: BLE001
        return False


def _check_migrations() -> bool:
    try:
        executor = MigrationExecutor(connection)
        return not executor.migration_plan(executor.loader.graph.leaf_nodes())
    except Exception:  # noqa: BLE001
        return False


@require_GET
def health_ready(request: HttpRequest) -> JsonResponse:
    checks = {"database": _check_db(), "cache": _check_cache()}
    checks["migrations"] = checks["database"] and _check_migrations()
    ok = all(checks.values())
    return JsonResponse({"status": "ok" if ok else "unavailable", "checks": checks}, status=200 if ok else 503)


def git_version() -> str:
    configured: str = getattr(settings, "ERP_GIT_SHA", "")
    if configured:
        return configured
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],  # noqa: S607
            cwd=settings.BASE_DIR,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


@require_GET
def system_environment(request: HttpRequest) -> JsonResponse:
    return JsonResponse(
        {
            "environment": "dev",
            "synthetic_data": True,
            "integrations": dict(settings.ERP_INTEGRATIONS),
            "version": git_version(),
        }
    )


def build_ui_context(ctx: tenancy.TenantContext, company_id: Any = None) -> dict[str, Any]:
    """Brand + context + current period (from the registered :class:`PeriodService`)."""
    periods = optional(PeriodService)
    company = None
    if company_id is not None:
        company = ctx.require_company(company_id)
    elif len(ctx.company_ids) == 1:
        company = next(iter(ctx.company_ids))
    period = None
    if periods is not None and company is not None:
        info = periods.current_period(ctx, company)
        period = info.to_json() if info else None
    return {
        "brand": {"name": settings.ERP_BRAND_NAME, "logo_path": settings.ERP_BRAND_LOGO_PATH or None},
        "environment": "dev",
        "tenant_id": str(ctx.tenant_id),
        "company_id": str(company) if company else None,
        "company_ids": sorted(str(c) for c in ctx.company_ids),
        "channel": ctx.channel.value,
        "timezone": settings.ERP_DEFAULT_COMPANY_TIMEZONE,
        "currency": settings.ERP_FUNCTIONAL_CURRENCY,
        "locale": "es-CO",
        "period": period,
    }


class UiContextView(APIView):  # type: ignore[misc]
    """``GET /api/v1/ui/context`` — authenticated principals only."""

    def get(self, request: Request) -> Response:
        ctx = tenancy.current()
        if ctx is None:
            raise TenancyNotBound("no tenant context for this principal")
        return Response(build_ui_context(ctx, request.query_params.get("company_id")))
