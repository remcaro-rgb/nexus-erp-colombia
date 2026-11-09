"""P22a security baseline on the running configuration (ASVS V3/V13/V14 config items, API8:2023)."""

from __future__ import annotations

import json

import pytest
from django.conf import settings
from qa_harness.evidence import scan_secrets

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "testserver", "::1"}


def test_p22a_debug_off_and_hosts_restricted():
    assert settings.DEBUG is False
    assert "*" not in settings.ALLOWED_HOSTS and "0.0.0.0" not in settings.ALLOWED_HOSTS  # noqa: S104
    for origin in settings.CSRF_TRUSTED_ORIGINS:
        assert "*" not in origin
        assert origin.split("://", 1)[1].split(":")[0].split("/")[0] in LOOPBACK_HOSTS, origin


def test_p22a_no_permissive_cors_or_allowany_default():
    assert "corsheaders" not in settings.INSTALLED_APPS or not getattr(settings, "CORS_ALLOW_ALL_ORIGINS", False)
    perms = settings.REST_FRAMEWORK.get("DEFAULT_PERMISSION_CLASSES", [])
    assert perms and not any(p.endswith("AllowAny") for p in perms)
    assert settings.MIDDLEWARE[0] == "django.middleware.security.SecurityMiddleware"
    assert "django.middleware.csrf.CsrfViewMiddleware" in settings.MIDDLEWARE


@pytest.mark.django_db
def test_p22a_security_headers_and_no_cross_origin_grant(client):
    resp = client.get("/api/health/live", headers={"Origin": "https://evil.example"})
    assert resp["X-Content-Type-Options"] == "nosniff"
    assert resp["X-Frame-Options"] == "DENY"
    assert resp.get("Referrer-Policy")
    assert "Access-Control-Allow-Origin" not in resp
    assert "Server" not in resp or "Django" not in resp["Server"]


@pytest.mark.django_db
def test_p22a_environment_endpoint_exposes_no_secrets(client):
    resp = client.get("/api/v1/system/environment")
    assert resp.status_code == 200
    body = resp.json()
    assert body["synthetic_data"] is True
    assert set(body["integrations"].values()) <= {"DISABLED", "FIXTURE_NOT_CERTIFIED", "SANDBOX", "LOCAL_CAPTURE"}
    text = json.dumps(body)
    assert not scan_secrets(text)
    assert settings.SECRET_KEY not in text
    for word in ("password", "secret", "token", "private_key"):
        assert word not in text.lower(), word


@pytest.mark.django_db
def test_p22a_error_responses_are_problem_json_without_internals(client):
    resp = client.get("/api/v1/ui/context")
    assert resp.status_code in (401, 403)
    assert resp["Content-Type"].startswith("application/problem+json")
    problem = resp.json()
    assert {"type", "title", "status", "code"} <= set(problem)
    assert "Traceback" not in resp.content.decode()
