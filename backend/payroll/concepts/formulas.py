"""Resolution of the workflow formulas (domain ``payroll``) referenced by concepts.

The formula lifecycle (DRAFT → PENDING_APPROVAL → ACTIVE, author ≠ approver, immutable ACTIVE
versions) is P07's ``workflow.formula.lifecycle``; payroll only reads it. A concept names one
formula row (``formula_id``); the calculation always uses the ACTIVE version of that formula's
code effective on the calculation date (``formula_not_approved`` otherwise), and the payslip line
stores that version's id, number and hash.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from django.db.models import Q

from kernel.errors import ValidationFailed
from payroll.concepts.graph import FormulaRef
from workflow.formula.errors import FormulaNotApproved

PAYROLL_DOMAIN = "payroll"


def _formula_model() -> Any:
    from workflow.models import Formula

    return Formula


def referenced(tenant_id: uuid.UUID, company_id: uuid.UUID, formula_id: uuid.UUID) -> Any:
    """The formula row a concept references (any status); same tenant, company and domain payroll."""
    row = _formula_model().objects.filter(tenant_id=tenant_id, company_id=company_id, id=formula_id).first()
    if row is None:
        raise ValidationFailed("formula_id does not name a formula of this company", field="formula_id")
    if row.domain != PAYROLL_DOMAIN:
        raise ValidationFailed(
            f"formula {row.code} belongs to domain {row.domain!r}; payroll concepts need domain 'payroll'",
            field="formula_id",
        )
    return row


def as_ref(row: Any) -> FormulaRef:
    return FormulaRef(
        id=str(row.id),
        code=row.code,
        version=int(row.formula_version),
        hash=row.hash,
        expression=row.expression,
        inputs=row.inputs or {},
        result_type=row.result_type,
        output_places=row.output_places,
        output_rounding=row.output_rounding or "HALF_EVEN",
    )


def active_version(tenant_id: uuid.UUID, company_id: uuid.UUID, formula_id: uuid.UUID, on: dt.date) -> FormulaRef:
    Formula = _formula_model()
    row = referenced(tenant_id, company_id, formula_id)
    active = (
        Formula.objects.filter(
            tenant_id=tenant_id, company_id=company_id, domain=PAYROLL_DOMAIN, code=row.code, status=Formula.ACTIVE
        )
        .filter(Q(effective_from__isnull=True) | Q(effective_from__lte=on))
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=on))
        .order_by("-formula_version")
        .first()
    )
    if active is None:
        raise FormulaNotApproved(
            f"no ACTIVE version of payroll formula '{row.code}' on {on.isoformat()}", formula=row.code
        )
    return as_ref(active)
