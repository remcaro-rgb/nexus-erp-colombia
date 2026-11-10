"""Pytest fixtures shared by the einvoice tests (imported by ``einvoice/tests/conftest.py``)."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

ALL_PERMISSIONS = tuple(
    f"einvoice.{p}"
    for p in (
        "document.read", "document.update", "document.export", "numbering_range.read", "numbering_range.create",
        "numbering_range.update", "numbering_range.delete", "numbering_range.admin", "contingency.read",
        "contingency.admin", "radian_event.read", "radian_event.create", "habilitation.read", "habilitation.create",
        "habilitation.update", "certificate.read", "settings.read", "settings.admin", "provider.read",
        "provider.create", "provider.update", "provider.delete", "provider.admin", "provider.export",
    )
)  # fmt: skip

_contexts: dict[str, Any] = {}


def resolve_context(request: Any) -> Any:
    """Test-only ``ERP_TENANT_CONTEXT_RESOLVER``: the context registered under ``X-Test-Context``."""
    return _contexts.get(request.headers.get("X-Test-Context", ""))


class _User:
    is_authenticated = True
    pk = 1


@pytest.fixture(autouse=True)
def einvoice_secrets_dir(tmp_path: Path, settings: Any) -> Path:
    """Every test gets its own gitignored-like secrets directory (mode 0700)."""
    directory = tmp_path / "einvoice-secrets"
    directory.mkdir(mode=0o700)
    settings.ERP_EINVOICE_SECRETS_DIR = str(directory)
    return directory


def write_secret(directory: Path, name: str, value: str) -> str:
    path = directory / name
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)
    return f"secret://einvoice/{name}"


@pytest.fixture
def ctx() -> Any:
    from kernel.testing.tenancy import make_context

    return make_context()


@pytest.fixture
def company(ctx: Any) -> uuid.UUID:
    from kernel.testing.tenancy import first_company

    return first_company(ctx)


@pytest.fixture
def policy() -> Iterator[Any]:
    from kernel import services
    from kernel.interfaces.policy import PolicyService
    from kernel.testing.fakes import FakePolicyService

    fake = FakePolicyService()
    as_policy: PolicyService = fake
    with services.override(PolicyService, as_policy):
        yield fake


def grant_all(policy: Any, ctx: Any, permissions: tuple[str, ...] = ALL_PERMISSIONS) -> None:
    for perm in permissions:
        policy.grant(ctx.principal_id, perm)


class ApiClient:
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
def api(settings: Any, ctx: Any) -> ApiClient:
    settings.ERP_TENANT_CONTEXT_RESOLVER = "einvoice.testing.fixtures.resolve_context"
    return ApiClient(ctx)


def invoice_model(number: str = "SETP990000001", outcome: str = "accept", **over: Any) -> Any:
    from einvoice.model import FiscalDocumentModel

    data: dict[str, Any] = {
        "family": "FE",
        "number": number,
        "issue_date": "2026-11-02",
        "issue_time": "10:00:00-05:00",
        "supplier": {"id_number": "900000001", "check_digit": "1", "name": "EMPRESA SINTÉTICA S.A.S."},
        "customer": {"id_number": "800000002", "id_type": "31", "name": "CLIENTE SINTÉTICO", "email": "cliente@example.invalid"},
        "lines": [
            {"description": "Servicio sintético", "quantity": "2", "unit_price": "50000.00", "line_extension": "100000.00",
             "taxes": [{"scheme": "01", "rate": "19.00", "taxable": "100000.00", "amount": "19000.00"}]},
        ],
        "line_extension": "100000.00",
        "tax_exclusive": "100000.00",
        "tax_inclusive": "119000.00",
        "payable": "119000.00",
        "fixture_outcome": outcome,
    }  # fmt: skip
    data.update(over)
    return FiscalDocumentModel.from_dict(data)


def create_range(ctx: Any, company: uuid.UUID, *, code: str = "FE-SETP", prefix: str = "SETP",
                 family: str = "FE", technical_key_ref: str = "") -> Any:  # fmt: skip
    from einvoice.models import NumberingRange
    from kernel.uow import run_in_unit_of_work

    return run_in_unit_of_work(
        lambda u: NumberingRange.objects.create(
            tenant_id=ctx.tenant_id, company_id=company, code=code, family=family, prefix=prefix,
            from_number=990000000, to_number=995000000, resolution="18760000001 (SINTÉTICA)",
            valid_from=dt.date(2026, 1, 1), valid_to=dt.date(2027, 12, 31), technical_key_ref=technical_key_ref,
        ),
        ctx,
    )  # fmt: skip


def register(ctx: Any, company: uuid.UUID, model: Any, source_id: uuid.UUID | None = None) -> Any:
    from einvoice import service
    from kernel.interfaces.common import DocumentRef
    from kernel.uow import run_in_unit_of_work

    ref = DocumentRef("sales.invoice", source_id or uuid.uuid4(), model.number)
    return run_in_unit_of_work(lambda u: service.register_document(u, company, ref, model), ctx)


ZERO = Decimal(0)
