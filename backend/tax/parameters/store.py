"""DB-backed :class:`kernel.parameters.ParameterProvider` (C1-9).

Resolution for ``get(code, at_date, company, scope_ref="")``: only APPROVED,
ACTIVE rows of the current tenant effective on ``at_date``; a company row wins
over a tenant-wide row; a ``scope_ref`` (e.g. a municipality DANE code) must
match exactly when given. then applies the
fail-closed policy (:func:`enforce`; UNKNOWN/missing → ``parameter_unknown``; production rules
without a legal-review stamp → ``parameter_not_verified``).
"""

from __future__ import annotations

import datetime as dt
import uuid

from django.conf import settings
from django.db.models import Q

from kernel import tenancy
from kernel.dates import effective_q
from kernel.errors import ParameterNotVerified, ParameterUnknown
from kernel.parameters import ParameterStatus, ParameterValue
from tax.models import Parameter
from tax.parameters import values


def enforce(value: ParameterValue | None, code: str, *, production: bool) -> ParameterValue:
    """Same policy as ``kernel.parameters.enforce`` with stable error codes.

    The kernel helper passes ``code=<parameter code>`` to the exception, which
    replaces the error code (``"uvt"`` instead of ``parameter_unknown``); see
    CCR P06-2. The parameter code travels in ``extra["parameter"]`` here.
    """
    if value is None or value.status is ParameterStatus.UNKNOWN:
        raise ParameterUnknown(f"parameter {code} is UNKNOWN for the requested date", parameter=code)
    if production and (value.synthetic or not value.legal_review_stamp):
        raise ParameterNotVerified(f"parameter {code} has no legal-review stamp", parameter=code)
    return value


def production_rules_default() -> bool:
    """DEV-only edition: production rules are off unless explicitly enabled for a test or a rehearsal."""
    return bool(getattr(settings, "ERP_PARAMETERS_PRODUCTION_RULES", False))


def to_value(row: Parameter) -> ParameterValue:
    return ParameterValue(
        code=row.code,
        value=values.typed(row.value_type, row.value),
        status=ParameterStatus(row.parameter_status),
        source_id=row.source.register_id or str(row.source_id),
        valid_from=row.valid_from,
        valid_to=row.valid_to,
        legal_review_stamp=(
            f"{row.legal_reviewed_by}@{row.legal_reviewed_at.isoformat()}"
            if row.legal_reviewed_by and row.legal_reviewed_at
            else None
        ),
        synthetic=row.synthetic,
    )


def effective_rows(tenant_id: uuid.UUID, code: str, at_date: dt.date, company: uuid.UUID | None, scope_ref: str = ""):  # type: ignore[no-untyped-def]
    company_q = Q(company_id__isnull=True) | Q(company_id=company) if company else Q(company_id__isnull=True)
    return (
        Parameter.objects.select_related("source")
        .filter(
            tenant_id=tenant_id, code=code, approval=Parameter.Approval.APPROVED, status="ACTIVE", scope_ref=scope_ref
        )
        .filter(company_q)
        .filter(effective_q(at_date))
        .order_by("company_id", "-valid_from")  # NULLs last in PostgreSQL ASC → company rows first
    )


class DbParameterProvider:
    """Implements ``kernel.parameters.ParameterProvider`` over ``tax_parameter``."""

    def __init__(self, production: bool | None = None, tenant_id: uuid.UUID | None = None) -> None:
        self._production = production
        self._tenant_id = tenant_id

    @property
    def production(self) -> bool:
        return production_rules_default() if self._production is None else self._production

    def _tenant(self) -> uuid.UUID:
        return self._tenant_id or tenancy.require_current().tenant_id

    def lookup(
        self, code: str, at_date: dt.date, company: uuid.UUID | None, scope_ref: str = ""
    ) -> ParameterValue | None:
        row = effective_rows(self._tenant(), code, at_date, company, scope_ref).first()
        return to_value(row) if row is not None else None

    def get(self, code: str, at_date: dt.date, company: uuid.UUID, scope_ref: str = "") -> ParameterValue:
        return enforce(self.lookup(code, at_date, company, scope_ref), code, production=self.production)
