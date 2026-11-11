"""T-X-DATAIO-01 (integration, G3): every template — a valid file applies, an invalid file applies zero rows with
row-level errors, and export → import round trip is a no-op (FEATURES-ADDENDUM F-X-DATAIO).

All data is SYNTHETIC (NOT LEGALLY VERIFIED). Each case lists the rows of a valid file and one invalid row that is
appended to a further valid row: the whole invalid file must be rejected with that row's error.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from dataio import service
from dataio.errors import ImportValidationFailed
from dataio.models import ImportStatus
from dataio.templates import registry
from dataio.testing import helpers as h
from tax.exogena.loader import synthetic_pack_data

pytestmark = pytest.mark.django_db


def gtin(body: str) -> str:
    """``body`` + GS1 mod-10 check digit (synthetic restricted-circulation prefix 2x, D-004)."""
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return body + str((10 - total % 10) % 10)


def pack(code: str, version: str = "1") -> str:
    data = synthetic_pack_data()
    data["pack_code"] = code
    data["version"] = version
    return json.dumps(data, ensure_ascii=False)


#: entity → (valid rows, extra valid row of the invalid file, invalid row, expected error code of the invalid row)
CASES: dict[str, tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any], str]] = {
    "dimensions": (
        [
            {
                "dimension": "business_unit",
                "code": "UN-RET",
                "description": "Retail",
                "node_type": "DETALLE",
                "parent_code": "UN",
                "valid_from": "2026-01-01",
            },
            {
                "dimension": "business_unit",
                "code": "UN",
                "description": "Unidades",
                "node_type": "AGRUPADOR",
                "parent_code": "",
                "valid_from": "2026-01-01",
            },
        ],
        {
            "dimension": "business_unit",
            "code": "UN-2",
            "description": "otra",
            "node_type": "DETALLE",
            "parent_code": "UN",
        },
        {"dimension": "business_unit", "code": "UN-X", "description": "mala", "node_type": "HOJA", "parent_code": "UN"},
        "enum",
    ),
    "dimension-rules": (
        [
            {
                "dimension": "cost_center",
                "scope_type": "account_range",
                "scope_ref": "51*",
                "mode": "REQUIRED",
                "priority": "10",
            }
        ],
        {
            "dimension": "aux_abierto",
            "scope_type": "account",
            "scope_ref": "519595",
            "mode": "ALLOWED",
            "priority": "0",
        },
        {"dimension": "cost_center", "scope_type": "galaxy", "scope_ref": "52*", "mode": "REQUIRED", "priority": "0"},
        "enum",
    ),
    "branches": (
        [
            {
                "code": "MED",
                "name": "Medellín",
                "is_head_office": "false",
                "municipality_code": "05001",
                "valid_from": "2026-01-01",
                "status": "ACTIVE",
            }
        ],
        {"code": "CAL", "name": "Cali", "is_head_office": "false", "valid_from": "2026-01-01"},
        {
            "code": "BAQ",
            "name": "Barranquilla",
            "is_head_office": "false",
            "municipality_code": "8001",
            "valid_from": "2026-01-01",
        },
        "validation_failed",
    ),
    "treasury-concepts": (
        [
            {
                "code": "PRESTAMO",
                "name": "Préstamo recibido",
                "direction": "IN",
                "account_role": "account:210505",
                "requires_dimension": "false",
                "requires_party": "true",
            }
        ],
        {"code": "REEMB", "name": "Reembolso", "direction": "OUT", "account_role": "EXPENSE"},
        {"code": "MALO", "name": "Malo", "direction": "SIDEWAYS", "account_role": "EXPENSE"},
        "enum",
    ),
    "issue-concepts": (
        [
            {
                "code": "CONSUMO",
                "name": "Consumo interno",
                "kind": "CONSUMPTION",
                "account_role": "EXPENSE",
                "requires_dimension": "true",
                "required_dims": "cost_center|business_unit",
                "status": "ACTIVE",
            }
        ],
        {"code": "MUESTRA", "name": "Muestras", "kind": "SAMPLE", "account_role": "EXPENSE"},
        {
            "code": "DONA",
            "name": "Donación",
            "kind": "DONATION",
            "account_role": "EXPENSE",
            "approval_threshold_value": "mil",
        },
        "type",
    ),
    "presentations": (
        [
            {
                "item_code": "ARROZ",
                "code": "CAJA12",
                "name": "Caja x12",
                "uom": "caja",
                "factor": "12",
                "precision": "0",
                "is_default_purchase": "true",
                "status": "ACTIVE",
            }
        ],
        {"item_code": "ARROZ", "code": "PACA6", "name": "Paca x6", "uom": "paca", "factor": "6"},
        {"item_code": "NO-EXISTE", "code": "CAJA", "name": "Caja", "uom": "caja", "factor": "12"},
        "not_found",
    ),
    "locations": (
        [
            {
                "warehouse_code": "PRINCIPAL",
                "code": "A-01-1",
                "name": "Estante 1",
                "location_type": "INTERNAL",
                "parent_code": "A-01",
                "allow_mixed_lots": "true",
            },
            {
                "warehouse_code": "PRINCIPAL",
                "code": "A-01",
                "name": "Pasillo A",
                "location_type": "VIEW",
                "parent_code": "ALMACEN",
                "allow_mixed_lots": "true",
            },
        ],
        {
            "warehouse_code": "PRINCIPAL",
            "code": "B-01",
            "name": "Pasillo B",
            "location_type": "INTERNAL",
            "parent_code": "ALMACEN",
        },
        {
            "warehouse_code": "PRINCIPAL",
            "code": "C-01",
            "name": "Pasillo C",
            "location_type": "INTERNAL",
            "parent_code": "NO-HAY",
        },
        "not_found",
    ),
    "gtins": (
        [{"value": gtin("770123456789"), "owner_type": "item", "item_code": "ARROZ"}],
        {"value": gtin("770123456790"), "owner_type": "item", "item_code": "HARINA"},
        {
            "value": "7701234567890" if gtin("770123456789")[-1] != "0" else "7701234567891",
            "owner_type": "item",
            "item_code": "PAN",
        },
        "barcode_checkdigit_invalid",
    ),
    "boms": (
        [
            {
                "code": "PAN-01",
                "name": "Pan tajado",
                "item_code": "PAN",
                "bom_type": "PRODUCTION",
                "output_qty": "10",
                "valid_from": "2026-01-01",
                "component_code": "HARINA",
                "quantity": "5",
                "status": "DRAFT",
            },
            {
                "code": "PAN-01",
                "name": "Pan tajado",
                "item_code": "PAN",
                "bom_type": "PRODUCTION",
                "output_qty": "10",
                "valid_from": "2026-01-01",
                "component_code": "LEVADURA",
                "quantity": "0.1",
                "status": "DRAFT",
            },
        ],
        {
            "code": "PAN-02",
            "name": "Pan integral",
            "item_code": "PAN",
            "output_qty": "5",
            "valid_from": "2026-01-01",
            "component_code": "HARINA",
            "quantity": "3",
        },
        {
            "code": "PAN-03",
            "name": "Pan raro",
            "item_code": "PAN",
            "output_qty": "5",
            "valid_from": "2026-01-01",
            "component_code": "HARINA",
            "quantity": "muchos",
        },
        "type",
    ),
    "portioning-templates": (
        [
            {
                "code": "RES-01",
                "name": "Despiece de res",
                "input_item_code": "RES",
                "tolerance_percent": "2",
                "waste_percent": "5",
                "output_code": "LOMO",
                "expected_yield_percent": "20",
                "is_main": "true",
            },
            {
                "code": "RES-01",
                "name": "Despiece de res",
                "input_item_code": "RES",
                "tolerance_percent": "2",
                "waste_percent": "5",
                "output_code": "MOLIDA",
                "expected_yield_percent": "75",
                "is_main": "false",
            },
        ],
        {
            "code": "RES-02",
            "name": "Solo molida",
            "input_item_code": "RES",
            "output_code": "MOLIDA",
            "expected_yield_percent": "90",
            "is_main": "true",
        },
        {
            "code": "RES-03",
            "name": "Imposible",
            "input_item_code": "RES",
            "waste_percent": "10",
            "output_code": "LOMO",
            "expected_yield_percent": "95",
            "is_main": "true",
        },
        "validation_failed",
    ),
    "price-formulas": (
        [
            {
                "code": "MAYORISTA",
                "name": "Precio mayorista",
                "formula_code": "PRECIO_MAYOR",
                "priority": "100",
                "valid_from": "2026-01-01",
                "scope_customer_group": "MAYORISTAS",
                "scope_currency": "COP",
                "status": "ACTIVE",
            }
        ],
        {
            "code": "ARROZ-ESP",
            "formula_code": "PRECIO_MAYOR",
            "priority": "200",
            "valid_from": "2026-01-01",
            "scope_item_code": "ARROZ",
        },
        {"code": "SIN-FORMULA", "formula_code": "NO_EXISTE", "valid_from": "2026-01-01"},
        "validation_failed",
    ),
    "payroll-concepts": (
        [
            {
                "code": "BONO_SINT",
                "name": "Bonificación (SINTÉTICO)",
                "concept_type": "DEVENGO",
                "account_role": "PAYROLL_EXPENSE",
                "salary_base": "false",
            }
        ],
        {"code": "AUX_SINT", "name": "Auxilio (SINTÉTICO)", "concept_type": "DEVENGO", "formula_code": "BONO_FORM"},
        {"code": "DESC_SINT", "name": "Descuento", "concept_type": "RETENCION"},
        "enum",
    ),
    "exogena-format-packs": (
        [{"pack_code": "SYN-EXO-T1", "year": "2026", "version": "1", "pack": pack("SYN-EXO-T1")}],
        {"pack_code": "SYN-EXO-T2", "year": "2026", "version": "1", "pack": pack("SYN-EXO-T2")},
        {"pack_code": "SYN-EXO-T3", "year": "2026", "version": "1", "pack": pack("SYN-EXO-OTRO")},
        "pack_mismatch",
    ),
    "external-account-invitations": (
        [{"email": "proveedor1@example.invalid", "portal_kind": "supplier", "party_code": "PRV-001"}],
        {"email": "proveedor2@example.invalid", "portal_kind": "supplier", "party_code": "PRV-001"},
        {"email": "proveedor3@example.invalid", "portal_kind": "supplier", "party_code": "PRV-999"},
        "not_found",
    ),
    "internal-requisitions": (
        [
            {
                "reference": "REQ-IMP-1",
                "document_date": "2026-11-09",
                "needed_by": "2026-11-20",
                "requesting_warehouse_code": "PRINCIPAL",
                "priority": "NORMAL",
                "item_code": "ARROZ",
                "quantity": "10",
                "status": "DRAFT",
            },
            {
                "reference": "REQ-IMP-1",
                "document_date": "2026-11-09",
                "needed_by": "2026-11-20",
                "requesting_warehouse_code": "PRINCIPAL",
                "priority": "NORMAL",
                "item_code": "HARINA",
                "quantity": "2.5",
                "status": "DRAFT",
            },
        ],
        {
            "reference": "REQ-IMP-2",
            "needed_by": "2026-11-21",
            "requesting_warehouse_code": "PRINCIPAL",
            "item_code": "PAN",
            "quantity": "3",
        },
        {
            "reference": "REQ-IMP-3",
            "needed_by": "2026-11-21",
            "requesting_warehouse_code": "PRINCIPAL",
            "item_code": "NO-EXISTE",
            "quantity": "3",
        },
        "not_found",
    ),
}


def test_T_X_DATAIO_01_every_registered_template_has_a_case():
    assert sorted(CASES) == sorted(registry.all_templates())
    for tpl in registry.all_templates().values():
        d = tpl.describe()
        assert d["columns"] and all(c["help"] for c in d["columns"]), tpl.entity
        assert d["examples"] or tpl.entity == "exogena-format-packs", tpl.entity


def _snapshot(w, entity):
    return h.table(h.export(w.ctx(), w.companies["ALFA"], entity, "csv"))


@pytest.mark.parametrize("entity", sorted(CASES))
def test_T_X_DATAIO_01_valid_applies_invalid_applies_zero_rows_roundtrip_noop(w, entity):
    ctx, alfa = w.ctx(), w.companies["ALFA"]
    valid, extra_ok, bad, code = CASES[entity]
    empty = _snapshot(w, entity)

    # 1. valid file applies (dry-run first: zero rows, preview matches the commit)
    dry = h.validate(ctx, alfa, entity, h.csv_bytes(valid))
    assert dry.import_status == ImportStatus.VALIDATED, dry.errors
    assert _snapshot(w, entity) == empty
    job = service.commit(ctx, alfa, dry.id)
    assert job.import_status == ImportStatus.COMMITTED, job.errors
    assert job.counts["created"] >= 1 and job.counts["failed"] == 0
    assert [p["action"] for p in dry.preview] == [p["action"] for p in job.results]
    applied = _snapshot(w, entity)
    assert len(applied) > len(empty)

    # 2. invalid file (a valid row + an invalid one): rejected with row-level errors, zero rows applied
    rows = [extra_ok, bad]
    bad_job = h.start(ctx, alfa, entity, h.csv_bytes(rows, headers=list(dict.fromkeys([*extra_ok, *bad]))))
    with pytest.raises(ImportValidationFailed) as exc:
        service.commit(ctx, alfa, bad_job.id)
    assert [(e["row"], e["code"]) for e in exc.value.rows] == [(3, code)], exc.value.rows
    assert _snapshot(w, entity) == applied

    # 3. export → import is a no-op in every format; the same changing batch is never applied twice
    for fmt in ("csv", "xlsx", "jsonl"):
        data = h.export(ctx, alfa, entity, fmt)
        again = h.commit(ctx, alfa, entity, data, filename=f"export.{fmt}")
        assert again.import_status == ImportStatus.COMMITTED, again.errors
        assert again.counts["created"] == again.counts["updated"] == 0, again.results
        assert again.effective is False
        assert _snapshot(w, entity) == applied
    # re-committing the step-1 file itself is an all-unchanged no-op as well
    same = h.commit(ctx, alfa, entity, h.csv_bytes(valid))
    assert same.counts["created"] == same.counts["updated"] == 0 and _snapshot(w, entity) == applied
