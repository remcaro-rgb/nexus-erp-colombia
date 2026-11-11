"""Field classifications of the issue resources (iam field policy; contract ``x-classification``).

Cost/value fields are ``confidential``: roles whose ``max_classification`` is ``internal`` (sales, POS,
portal) never see them — also not inside ``lines`` (the views strip line costs with the same rule).
"""

from __future__ import annotations

COMMON = {
    "id": "internal",
    "tenant_id": "internal",
    "company_id": "internal",
    "version": "internal",
    "status": "internal",
}

FIELDS: dict[str, dict[str, str]] = {
    "inventory.issue_concept": {
        **COMMON,
        "code": "internal",
        "name": "internal",
        "kind": "internal",
        "account_role": "internal",
        "requires_dimension": "internal",
        "required_dims": "internal",
        "requires_reason": "internal",
        "reason_codes": "internal",
        "requires_approval": "internal",
        "approval_threshold_value": "confidential",
        "audit": "internal",
    },
    "inventory.issue": {
        **COMMON,
        "number": "internal",
        "document_date": "internal",
        "posting_date": "internal",
        "currency": "public",
        "total": "confidential",
        "line_costs": "confidential",
        "notes": "internal",
        "source": "internal",
        "concept_id": "internal",
        "concept_code": "internal",
        "warehouse_id": "internal",
        "branch_id": "internal",
        "reason": "internal",
        "dimensions": "internal",
        "lines": "internal",
        "approval_id": "internal",
        "requested_by": "internal",
        "journal_entry_id": "internal",
        "reversal_entry_id": "internal",
        "reversal_reason": "internal",
        "posted_at": "internal",
        "reversed_at": "internal",
        "audit": "internal",
        # :simulate result (ActionResult)
        "resource": "internal",
        "data": "confidential",
        "warnings": "internal",
    },
}


def register_fields() -> None:
    from iam.policy import fields as field_registry

    for resource, fields in FIELDS.items():
        field_registry.register(resource, fields)
