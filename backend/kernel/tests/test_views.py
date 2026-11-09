import datetime as dt
import uuid

import pytest
from django.test import Client
from rest_framework.test import APIRequestFactory, force_authenticate

from kernel import services, tenancy, views
from kernel.errors import TenancyNotBound, ValidationFailed
from kernel.interfaces.periods import PeriodInfo, PeriodService
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


def test_ui_context_view_authenticated():
    class User:
        is_authenticated = True

    factory = APIRequestFactory()
    view = views.UiContextView.as_view()
    request = factory.get("/api/v1/ui/context")
    force_authenticate(request, user=User())
    ctx = make_context()
    with tenancy.activate(ctx):
        r = view(request)
    assert r.status_code == 200 and r.data["tenant_id"] == str(ctx.tenant_id)
    request = factory.get("/api/v1/ui/context")
    force_authenticate(request, user=User())
    r = view(request)
    assert r.status_code == 500 and r.data["code"] == TenancyNotBound.code
