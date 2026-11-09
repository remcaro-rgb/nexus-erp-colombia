"""Pytest fixtures shared by the P06 test packages (imported by their conftests)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest

ALL_TAX_PERMISSIONS = (
    "tax.parameter.read", "tax.parameter.create", "tax.parameter.update", "tax.parameter.approve",
    "tax.source.read", "tax.source.create", "tax.computation.read", "tax.nit.read", "tax.municipality.read",
    "tax.holiday.read", "tax.exogena_format_pack.read", "tax.exogena_format_pack.create",
    *(f"tax.{r}.{a}" for r in ("tax_code", "withholding_concept", "ica_tariff", "party_tax_profile", "calendar")
      for a in ("read", "create", "update", "delete")),
)  # fmt: skip

_contexts: dict[str, Any] = {}


def resolve_context(request: Any) -> Any:
    """Test-only ``ERP_TENANT_CONTEXT_RESOLVER``: the context registered under ``X-Test-Context``."""
    return _contexts.get(request.headers.get("X-Test-Context", ""))


class _User:
    is_authenticated = True
    pk = 1


@pytest.fixture
def p06_ctx() -> Any:
    from kernel.testing.tenancy import make_context

    return make_context()


@pytest.fixture
def company(p06_ctx: Any) -> uuid.UUID:
    from kernel.testing.tenancy import first_company

    return first_company(p06_ctx)


@pytest.fixture
def policy() -> Iterator[Any]:
    from kernel import services
    from kernel.interfaces.policy import PolicyService
    from kernel.testing.fakes import FakePolicyService

    fake = FakePolicyService()
    as_policy: PolicyService = fake
    with services.override(PolicyService, as_policy):
        yield fake


def grant_all(policy: Any, ctx: Any, permissions: tuple[str, ...] = ALL_TAX_PERMISSIONS) -> None:
    for perm in permissions:
        policy.grant(ctx.principal_id, perm)


@pytest.fixture
def seeded(db: Any, p06_ctx: Any, company: uuid.UUID) -> Any:
    """Tenant with the 2026 parameter seed, default tax codes for the company and the synthetic exógena pack."""
    from kernel.uow import run_in_unit_of_work
    from tax.wiring import seed_tax

    run_in_unit_of_work(lambda uow: seed_tax(uow, {"companies": [str(company)]}), p06_ctx)
    return p06_ctx


class ApiClient:
    """APIClient bound to a tenant context through the test resolver."""

    def __init__(self, ctx: Any) -> None:
        from rest_framework.test import APIClient

        self.client = APIClient()
        self.client.force_authenticate(user=_User())
        self.use(ctx)

    def use(self, ctx: Any) -> None:
        key = str(uuid.uuid4())
        _contexts[key] = ctx
        self.client.credentials(HTTP_X_TEST_CONTEXT=key)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.client, name)

    def command(self, method: str, url: str, body: Any = None, key: str | None = None, **headers: str) -> Any:
        extra = {f"HTTP_{k.upper().replace('-', '_')}": v for k, v in headers.items()}
        extra["HTTP_IDEMPOTENCY_KEY"] = key or f"test-{uuid.uuid4()}"
        return getattr(self.client, method)(url, body, format="json", **extra)


@pytest.fixture
def api(settings: Any, p06_ctx: Any) -> ApiClient:
    settings.ROOT_URLCONF = "tax.testing.urls"
    settings.ERP_TENANT_CONTEXT_RESOLVER = "tax.testing.fixtures.resolve_context"
    return ApiClient(p06_ctx)
