"""Field classifications of the P09c resources (iam field policy; contract ``x-classification``).

Cost and value fields are ``confidential``: sales/POS/portal roles (max ``internal``) never see them. Registered when
the BOM / transformation views are imported and by the tests (the core registry is owned by P09a).
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
    "inventory.bom": {
        **COMMON,
        "code": "internal",
        "name": "internal",
        "bom_status": "internal",
        "bom_type": "internal",
        "item_id": "internal",
        "presentation_id": "internal",
        "version_no": "internal",
        "valid_from": "internal",
        "valid_to": "internal",
        "output_qty": "internal",
        "yield_percent": "internal",
        "normal_waste_percent": "internal",
        "notes": "internal",
        "components": "internal",
    },
    "inventory.bom_explosion": {
        "id": "internal",
        "bom_id": "internal",
        "quantity": "internal",
        "date": "internal",
        "tree": "internal",
        "leaves": "internal",
        "total_cost": "confidential",
        "unit_cost": "confidential",
    },
    "inventory.portioning_template": {
        **COMMON,
        "code": "internal",
        "name": "internal",
        "input_item_id": "internal",
        "outputs": "internal",
        "tolerance_percent": "internal",
        "waste_percent": "internal",
        "rounding_rule": "internal",
        "notes": "internal",
    },
    "inventory.repack_rule": {
        **COMMON,
        "item_id": "internal",
        "from_presentation_id": "internal",
        "to_presentation_id": "internal",
        "from_factor": "internal",
        "to_factor": "internal",
    },
    "inventory.transformation": {
        **COMMON,
        "number": "internal",
        "transformation_type": "internal",
        "warehouse_id": "internal",
        "template_id": "internal",
        "bom_id": "internal",
        "planned_qty": "internal",
        "document_date": "internal",
        "posting_date": "internal",
        "notes": "internal",
        "reason": "internal",
        "currency": "public",
        "source": "internal",
        "lines": "internal",
        "inputs_reserved": "internal",
        "tolerance_percent": "internal",
        "approval_required": "internal",
        "approval_reasons": "internal",
        "approval_note": "internal",
        "approved_by": "internal",
        "journal_entry_id": "internal",
        "reversal_entry_id": "internal",
        "additional_costs": "confidential",
        "total": "confidential",
        "value_in": "confidential",
        "value_out": "confidential",
        "waste_expensed": "confidential",
    },
    "inventory.transformation_simulation": {
        "id": "internal",
        "balanced": "internal",
        "approval_required": "internal",
        "approval_reasons": "internal",
        "yield_checks": "internal",
        "allocation_basis": "internal",
        "qty_in": "internal",
        "qty_out": "internal",
        "abnormal_waste_qty": "internal",
        "lines": "internal",
        "value_in": "confidential",
        "additional_costs": "confidential",
        "waste_expensed": "confidential",
        "value_out": "confidential",
        "journal": "confidential",
    },
}

#: line keys that carry cost (stripped from nested ``lines`` when the principal cannot read confidential fields)
LINE_COST_KEYS = ("unit_cost", "value", "amount", "unit_price")


def register_fields() -> None:
    from iam.policy import fields as field_registry

    for resource, fields in FIELDS.items():
        field_registry.register(resource, fields)
