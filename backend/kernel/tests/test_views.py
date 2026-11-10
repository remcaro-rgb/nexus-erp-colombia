import datetime as dt
import uuid

import pytest
from django.test import Client
from rest_framework.test import APIRequestFactory, force_authenticate

from kernel import services, tenancy, views
from kernel.errors import TenancyNotBound, ValidationFailed
from kernel.interfaces.periods import PeriodInfo, PeriodService
from kernel.interfaces.ui import UiShellProvider
from kernel.testing.fakes import FakePeriodService
from kernel.testing.tenancy import first_company, make_context


@pytest.mark.django_db
def test_health_and_environment_are_anonymous_and_secret_free(settings):
    c = Client()
    assert c.get("/api/health/live").json() == {"status": "ok"}
    ready = c.get("/api/health/ready")
    assert ready.status_code == 200
    assert ready.json()["checks"] == {"database": True, "cache": True, "migrations": True}
    env = c.get("/api/v1/system/environment").json()
    assert env["environment"] == "dev" and env["synthetic_data"] is True
    assert env["integrations"]["dian"] == "FIXTURE_NOT_CERTIFIED"
    assert env["integrations"]["email"] == "LOCAL_CAPTURE"
    body = str(env).lower()
    for word in ("secret", "password", "token", settings.SECRET_KEY.lower()):
        assert word not in body
    assert c.post("/api/health/live").status_code == 405


@pytest.mark.django_db
def test_ready_reports_failures(monkeypatch):
    monkeypatch.setattr(views, "_check_cache", lambda: False)
    r = Client().get("/api/health/ready")
    assert r.status_code == 503 and r.json()["status"] == "unavailable"


@pytest.mark.django_db
def test_health_checks_handle_errors(monkeypatch):
    class Broken:
        def cursor(self):
            raise RuntimeError("db down")

    monkeypatch.setattr(views, "connection", Broken())
    assert views._check_db() is False
    assert views._check_migrations() is False
    monkeypatch.setattr(views.cache, "set", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("cache down")))
    assert views._check_cache() is False


def test_git_version(settings, monkeypatch):
    settings.ERP_GIT_SHA = "abc123"
    assert views.git_version() == "abc123"
    settings.ERP_GIT_SHA = ""
    assert views.git_version()
    import subprocess

    def boom(*a, **k):
        raise OSError("no git")

    monkeypatch.setattr(subprocess, "run", boom)
    assert views.git_version() == "unknown"


@pytest.mark.django_db
def test_ui_context_requires_authentication():
    r = Client().get("/api/v1/ui/context")
    assert r.status_code in (401, 403)
    assert r["Content-Type"].startswith("application/problem+json")
    assert r.json()["code"] in ("not_authenticated", "permission_denied")


def test_ui_context_payload_with_period(settings):
    settings.ERP_BRAND_NAME = "NexusERP"
    ctx = make_context()
    company = first_company(ctx)
    from kernel import clock

    today = clock.business_date()
    period = PeriodInfo(uuid.uuid4(), company, "2026-11", today.replace(day=1), today + dt.timedelta(days=1), "OPEN")
    with services.override(PeriodService, FakePeriodService([period])):
        data = views.build_ui_context(ctx)
        assert data["period"]["status"] == "OPEN" and data["period"]["timezone"] == "America/Bogota"
        assert data["brand"] == {"name": "NexusERP", "logo_path": None}
        assert data["company_id"] == str(company)
        with pytest.raises(ValidationFailed):
            views.build_ui_context(ctx, uuid.uuid4())
    multi = make_context(company_ids=[uuid.uuid4(), uuid.uuid4()])
    data = views.build_ui_context(multi)
    assert data["company_id"] is None and data["period"] is None
    assert views.build_ui_context(multi, sorted(multi.company_ids)[1])["company_id"] == str(
        sorted(multi.company_ids)[1]
    )


class _FakeShell:
    """Typed fake of kernel.interfaces.ui.UiShellProvider (no IAM import in kernel tests)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def context_extras(self, ctx, company_id):
        self.calls.append(("context", company_id))
        return {
            "companies": [{"id": str(company_id), "legal_name": "Sintética S.A.S.", "nit": "900000001-1", "code": "S"}],
            "roles": ["r"],
            "permissions": ["shell.access"],
            "user": {"display_name": "u", "email": "u@example.invalid"},
        }

    def modules(self, ctx, company_id):
        self.calls.append(("modules", company_id))
        return {"groups": ["Inicio"], "modules": []}


def test_ui_context_view_authenticated():
    """FIX-UI-LOGIN-W03 UL-2: the authenticated context is activated for the handler and the provider's extras merge."""

    class User:
        is_authenticated = True

    factory = APIRequestFactory()
    view = views.UiContextView.as_view()
    ctx = make_context()
    fake = _FakeShell()
    request = factory.get("/api/v1/ui/context")
    force_authenticate(request, user=User())
    request.erp_context = ctx  # what the IAM authentication sets
    with services.override(UiShellProvider, fake):
        r = view(request)
    assert r.status_code == 200 and r.data["tenant_id"] == str(ctx.tenant_id)
    assert r.data["companies"][0]["code"] == "S" and r.data["permissions"] == ["shell.access"]
    assert r.data["session_idle_minutes"] == 30 and r.data["feature_flags"] == {}
    assert fake.calls == [("context", first_company(ctx))]
    assert tenancy.current() is None  # closed in finalize_response
    # An authenticated principal without a context still fails closed.
    request = factory.get("/api/v1/ui/context")
    force_authenticate(request, user=User())
    with services.override(UiShellProvider, fake):
        r = view(request)
    assert r.status_code == 500 and r.data["code"] == TenancyNotBound.code


def test_fix_ui_login_w03_ui_modules_view_delegates_and_fails_closed():
    class User:
        is_authenticated = True

    factory = APIRequestFactory()
    view = views.UiModulesView.as_view()
    ctx = make_context()
    fake = _FakeShell()

    def call(params=None):
        request = factory.get("/api/v1/ui/modules", params or {})
        force_authenticate(request, user=User())
        request.erp_context = ctx
        return view(request)

    with services.override(UiShellProvider, fake):
        r = call({"company_id": str(first_company(ctx))})
        assert r.status_code == 200 and r.data == {"groups": ["Inicio"], "modules": []}
        assert call({"company_id": str(uuid.uuid4())}).status_code == 422  # not one of the principal's companies
    previous = services.optional(UiShellProvider)
    services.unregister(UiShellProvider)
    try:
        r = call()
        assert r.status_code == 503 and r.data["code"] == "service_unavailable"
    finally:
        if previous is not None:
            services.register(UiShellProvider, previous, replace=True)


@pytest.mark.django_db
def test_fix_ui_login_w03_ui_modules_requires_authentication():
    r = Client().get("/api/v1/ui/modules")
    assert r.status_code == 401
    assert r["Content-Type"].startswith("application/problem+json")
