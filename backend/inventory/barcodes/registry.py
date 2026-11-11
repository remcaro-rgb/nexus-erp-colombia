"""Field classifications of the barcode/label resources (iam field policy; contract ``x-classification``).

Registered when ``inventory.barcodes.models`` is imported (app loading), like the P09a core registry.
Labels and barcodes never carry cost fields.
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
    "inventory.barcode": {
        **COMMON,
        "value": "internal",
        "symbology": "internal",
        "owner_type": "internal",
        "owner_id": "internal",
        "item_id": "internal",
        "source": "internal",
        "gs1_prefix": "internal",
        "gs1_ownership_claimed": "internal",
        "label": "internal",
        "resource": "internal",
        "data": "internal",
        "warnings": "internal",
        "kind": "internal",
        "progress": "internal",
        "started_at": "internal",
        "finished_at": "internal",
        "result_links": "internal",
        "result": "internal",
    },
    "inventory.label_template": {
        **COMMON,
        "code": "internal",
        "name": "internal",
        "symbology": "internal",
        "layout": "internal",
        "size_mm": "internal",
        "audit": "internal",
    },
    "inventory.label_print_job": {
        **COMMON,
        "template_id": "internal",
        "targets": "internal",
        "copies": "internal",
        "label_count": "internal",
        "labels": "internal",
        "pdf_hash": "internal",
        "data_hash": "internal",
        "audit": "internal",
    },
}


def register_fields() -> None:
    from iam.policy import fields as field_registry

    for resource, fields in FIELDS.items():
        field_registry.register(resource, fields)
