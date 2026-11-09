"""P22a security suite against the real URLconf: anonymous callers are refused everywhere except the
three anonymous kernel endpoints (D-ALL 1, D-P01). Runs on every merge; grows with every module."""

from __future__ import annotations

import re

import pytest
from qa_harness import defects
from qa_harness.routes import implemented_routes

ALLOWED_ANONYMOUS = frozenset({"/api/health/live", "/api/health/ready", "/api/v1/system/environment"})
KNOWN = {("get", "/api/v1/schema"): "DEF-P22-003"}
METHODS = ("get", "post", "patch", "delete")
FILLER = "0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b"


def _concrete(template: str) -> str:
    return re.sub(r"\{[^}]+\}", FILLER, template)


def _protected():
    out = []
    for r in implemented_routes():
        if r.template in ALLOWED_ANONYMOUS:
            continue
        for method in METHODS:
            known = KNOWN.get((method, r.template))
            marks = [defects.xfail(known)] if known else []
            out.append(pytest.param(method, r.template, id=f"{method}-{r.template}", marks=marks))
    return out


PROTECTED = _protected()


def test_p22a_route_inventory_is_not_empty():
    templates = {r.template for r in implemented_routes()}
    assert templates >= ALLOWED_ANONYMOUS
    assert PROTECTED, "at least /api/v1/ui/context is protected"


@pytest.mark.django_db
@pytest.mark.parametrize("method,template", PROTECTED)
def test_p22a_anonymous_is_refused(client, method, template):
    kwargs = {"data": "{}", "content_type": "application/json"} if method in ("post", "patch") else {}
    resp = getattr(client, method)(_concrete(template), **kwargs)
    # 405 (method not offered) is a refusal too; anything 2xx/3xx is a hole.
    assert resp.status_code in (401, 403, 404, 405), f"{method.upper()} {template} -> {resp.status_code}"
    assert b"Traceback" not in resp.content


@pytest.mark.django_db
@pytest.mark.parametrize("template", sorted(ALLOWED_ANONYMOUS))
def test_p22a_anonymous_endpoints_are_read_only(client, template):
    assert client.get(template).status_code in (200, 503)
    assert client.post(template, data="{}", content_type="application/json").status_code == 405


@defects.xfail("DEF-P22-002")
@pytest.mark.django_db
def test_p22a_anonymous_gets_401_with_www_authenticate(client):
    """Contract: every protected operation declares 401 Unauthorized (problem+json) + OAuth bearer."""
    bad = []
    for r in implemented_routes():
        if r.template in ALLOWED_ANONYMOUS:
            continue
        resp = client.get(_concrete(r.template))
        if resp.status_code != 401 or "Bearer" not in resp.get("WWW-Authenticate", ""):
            bad.append(f"{r.template}: {resp.status_code} {resp.get('WWW-Authenticate', '')!r}")
    assert not bad, bad
