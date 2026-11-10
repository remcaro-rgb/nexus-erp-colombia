"""Health, environment and UI-shell endpoints (D-P01, UI task matrix P01).

``/api/health/live``, ``/api/health/ready`` and ``/api/v1/system/environment``
are the only anonymous endpoints; they return no secrets. ``/api/v1/ui/context``
and ``/api/v1/ui/modules`` require authentication (default DRF authentication: the
IAM bearer token; deny by default). The principal's tenant context set by the
authentication layer is activated for the handler (FIX-UI-LOGIN-W03 UL-2); the
principal data comes from the registered :class:`kernel.interfaces.ui.UiShellProvider`
(the kernel never imports ``iam``).
"""

from __future__ import annotations

import contextlib
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
from kernel.errors import ServiceUnavailable, TenancyNotBound
from kernel.interfaces.periods import PeriodService
from kernel.interfaces.ui import UiShellProvider
from kernel.services import optional


@require_GET
def health_live(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


def _check_db() -> bool:
    # ORM-level probe (no raw SQL, ADR-004): open the connection if needed, then ask the backend
    # whether it answers (Django's is_usable() issues the backend's own liveness query).
    try:
        connection.ensure_connection()
        return bool(connection.is_usable())
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


#: Realm ``erp-dev`` SSO idle timeout (``ssoSessionIdleTimeout`` 1800 s); the shell signs out after it.
SESSION_IDLE_MINUTES = 30


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


class _ContextView(APIView):  # type: ignore[misc]  # DRF ships no type stubs (Any base)
    """Authenticated kernel view: activates the request's tenant context for the handler.

    Authentication and permission classes are the DRF defaults (IAM bearer token, ``IsAuthenticated``):
    no token → 401 problem. The context set by the authentication layer (``request.erp_context``) is
    activated in :meth:`initial` and closed in :meth:`finalize_response`, as ``iam.api.base.SecuredView``
    does. An authenticated principal without a context fails closed (``tenancy_not_bound``).
    """

    _ctx_stack: contextlib.ExitStack | None = None

    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        super().initial(request, *args, **kwargs)
        ctx = getattr(request, "erp_context", None)
        if isinstance(ctx, tenancy.TenantContext):
            stack = contextlib.ExitStack()
            stack.enter_context(tenancy.activate(ctx))
            self._ctx_stack = stack

    def finalize_response(self, request: Request, response: Any, *args: Any, **kwargs: Any) -> Any:
        stack = self._ctx_stack
        if stack is not None:
            self._ctx_stack = None
            stack.close()
        return super().finalize_response(request, response, *args, **kwargs)

    @staticmethod
    def context() -> tenancy.TenantContext:
        ctx = tenancy.current()
        if ctx is None:
            raise TenancyNotBound("no tenant context for this principal")
        return ctx


class UiContextView(_ContextView):
    """``GET /api/v1/ui/context`` — brand, context, period and the principal's shell data."""

    def get(self, request: Request) -> Response:
        ctx = self.context()
        data = build_ui_context(ctx, request.query_params.get("company_id"))
        data["session_idle_minutes"] = SESSION_IDLE_MINUTES
        data["feature_flags"] = {}  # honest empty object until a feature-flag source exists
        provider = optional(UiShellProvider)
        if provider is not None:
            company = data["company_id"]
            data.update(provider.context_extras(ctx, ctx.require_company(company) if company else None))
        return Response(data)


class UiModulesView(_ContextView):
    """``GET /api/v1/ui/modules?company_id=`` — the module registry filtered server-side by permission."""

    def get(self, request: Request) -> Response:
        ctx = self.context()
        provider = optional(UiShellProvider)
        if provider is None:
            raise ServiceUnavailable("the UI module registry is not available")
        raw = request.query_params.get("company_id")
        company = ctx.require_company(raw) if raw else None
        return Response(provider.modules(ctx, company))
