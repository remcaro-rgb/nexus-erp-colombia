"""Registrations of the POS module: field classifications, the ``pos.config`` branch reference, startup guard.

Field classes mirror the contract ``x-classification`` plus the confidential cost fields (CCR P12-1): roles whose
``max_classification`` is ``internal`` (``cajero_pos``) never receive cost, valuation or drawer totals.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

COMMON = {"id": "public", "tenant_id": "internal", "company_id": "internal", "version": "internal"}

FIELDS: dict[str, dict[str, str]] = {
    "pos.config": {
        **COMMON,
        "name": "internal",
        "active": "internal",
        "branch_id": "internal",
        "warehouse_id": "internal",
        "location_id": "internal",
        "price_list_id": "internal",
        "numbering_range_id": "internal",
        "allowed_tenders": "internal",
        "receipt_profile": "internal",
        "cash_limit": "confidential",
        "allow_returns": "internal",
        "blind_count": "internal",
        "default_party_id": "internal",
    },
    "pos.terminal": {
        **COMMON,
        "code": "internal",
        "name": "internal",
        "status": "internal",
        "config_id": "internal",
        "device_fingerprint": "internal",
        "registered_at": "internal",
    },
    "pos.session": {
        **COMMON,
        "number": "internal",
        "status": "internal",
        "terminal_id": "internal",
        "config_id": "internal",
        "cashier_id": "internal",
        "opened_at": "internal",
        "closed_at": "internal",
        "business_date": "internal",
        "opening_cash": "confidential",
        "counted_cash": "confidential",
        "expected_cash": "confidential",
        "difference": "confidential",
        "expected": "confidential",
        "counted": "confidential",
        "deposit_amount": "confidential",
        "blind": "internal",
        "journal_entry_ids": "internal",
    },
    "pos.ticket": {
        **COMMON,
        "number": "internal",
        "status": "internal",
        "client_uuid": "internal",
        "session_id": "internal",
        "document_date": "internal",
        "cashier_id": "internal",
        "branch_id": "internal",
        "warehouse_id": "internal",
        "party": "confidential",
        "currency": "public",
        "notes": "internal",
        "subtotal": "internal",
        "tax_total": "internal",
        "total": "internal",
        "change_due": "internal",
        "payments": "confidential",
        "cost_total": "confidential",
        "fiscal_doc_type": "internal",
        "fiscal_status": "internal",
        "cude": "internal",
        "journal_entry_id": "internal",
        "lines": "internal",
        "invoice_request": "restricted-pii",
    },
    "pos.ticket_line": {
        "line_no": "internal",
        "item_id": "internal",
        "presentation_id": "internal",
        "barcode": "internal",
        "description": "internal",
        "quantity": "internal",
        "factor": "internal",
        "base_qty": "internal",
        "unit_price": "internal",
        "discount_pct": "internal",
        "amount": "internal",
        "tax_code": "internal",
        "tax_amount": "internal",
        "price_source": "internal",
        "cost_value": "confidential",
        "unit_cost": "confidential",
    },
    "pos.return": {
        **COMMON,
        "number": "internal",
        "status": "internal",
        "session_id": "internal",
        "original": "internal",
        "document_date": "internal",
        "refund_tender": "internal",
        "reason": "internal",
        "notes": "internal",
        "subtotal": "internal",
        "tax_total": "internal",
        "total": "internal",
        "cost_total": "confidential",
        "journal_entry_id": "internal",
        "lines": "internal",
    },
    "pos.cash_movement": {
        **COMMON,
        "number": "internal",
        "status": "internal",
        "session_id": "internal",
        "movement_type": "internal",
        "amount": "confidential",
        "reason": "internal",
        "journal_entry_id": "internal",
    },
}

#: Environment switch of the ``[A]`` capability (never set in DEV; the code path is not built in this edition).
OFFLINE_SWITCH = "ERP_POS_OFFLINE_FISCAL_SELLING"
#: Path of the EG-13 evidence file (signed business/legal acceptance + PT/counsel validation, shared/09).
EVIDENCE_PATH = "ERP_POS_EG13_EVIDENCE"


def check_offline_flag(environment: str, switch: str | None, evidence_path: str | None) -> None:
    """T-P12-13: production refuses ``pos_offline_fiscal_selling=on`` without an APPROVED EG-13 evidence file."""
    if str(switch or "").strip().lower() not in ("1", "true", "on", "yes"):
        return
    if environment != "production":
        raise ImproperlyConfigured(
            "pos_offline_fiscal_selling is an [A] capability that is not built in this DEV-only edition (ADR-015)"
        )
    if not evidence_path:
        raise ImproperlyConfigured(f"pos_offline_fiscal_selling=on requires {EVIDENCE_PATH} (EG-13 evidence)")
    try:
        records = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ImproperlyConfigured("EG-13 evidence file unreadable") from exc
    rows = records if isinstance(records, list) else [records]
    if not any(isinstance(r, dict) and r.get("gate") == "EG-13" and r.get("status") == "APPROVED" for r in rows):
        raise ImproperlyConfigured("pos_offline_fiscal_selling=on requires an APPROVED EG-13 record")


def register_fields() -> None:
    from iam.policy import fields as field_registry

    for resource, fields in FIELDS.items():
        field_registry.register(resource, fields)


def register_branch_reference() -> None:
    from iam.branches import services as branch_services

    branch_services.register_reference(
        branch_services.BranchReference(
            branch_services.POS_REFERENCE, "pos_config", active_sql="active", label_column="name"
        )
    )


def register() -> None:
    from django.conf import settings

    check_offline_flag(
        str(getattr(settings, "ERP_ENVIRONMENT", "dev")), os.environ.get(OFFLINE_SWITCH), os.environ.get(EVIDENCE_PATH)
    )
    register_fields()
    register_branch_reference()
