"""F-ACC-EXO, P06 part: format-pack schema, loader, synthetic pack and rule engine.

T-ACC-EXO-01 (unit, G1): format pack schema validation rejects missing fields /
unknown types; the synthetic pack loads with legal_status NOT_DIAN_VALID.
"""

from __future__ import annotations

import copy
from decimal import Decimal
from typing import Any

import pytest

from tax.errors import ExogenaPackInvalid
from tax.exogena.loader import SYNTHETIC_PACK_CODE, load_pack, load_synthetic_pack, synthetic_pack_data
from tax.exogena.rules import validate_rows
from tax.exogena.schema import SYNTHETIC_LABEL, validate_pack


def _paths(problems: list[dict[str, str]]) -> set[str]:
    return {p["path"] for p in problems}


def _pack() -> dict[str, Any]:
    return copy.deepcopy(synthetic_pack_data())


# ----------------------------------------------------------------------------- T-ACC-EXO-01


def test_T_ACC_EXO_01_synthetic_pack_loads_not_dian_valid() -> None:
    pack = load_synthetic_pack()
    assert pack.pack_code == SYNTHETIC_PACK_CODE
    assert pack.source == "SYNTHETIC"
    assert pack.legal_status == "NOT_DIAN_VALID"
    assert pack.synthetic is True and pack.verified is False
    assert pack.label == SYNTHETIC_LABEL == "SINTÉTICO — no válido ante la DIAN"
    assert "SINTETICO_NO_VALIDO_DIAN" in pack.file_spec["file_name_pattern"]
    assert len(pack.checksum) == 64 and pack.checksum == load_synthetic_pack().checksum
    assert {f.name for f in pack.fields} >= {"concepto", "numero_identificacion", "dv", "pago_deducible"}
    assert all(c["code"].startswith("SYN-") for c in pack.concepts)  # no real DIAN concept codes


@pytest.mark.parametrize("key", ["pack_code", "format_code", "year", "version", "source", "legal_status", "fields",
                                 "file_spec"])  # fmt: skip
def test_T_ACC_EXO_01_rejects_missing_top_level_field(key: str) -> None:
    pack = _pack()
    del pack[key]
    assert key in _paths(validate_pack(pack))
    with pytest.raises(ExogenaPackInvalid) as exc:
        load_pack(pack)
    assert exc.value.code == "exogena_pack_invalid"
    assert any(p["path"] == key for p in exc.value.extra["problems"])


@pytest.mark.parametrize("key", ["name", "type", "length", "required"])
def test_T_ACC_EXO_01_rejects_field_missing_required_key(key: str) -> None:
    pack = _pack()
    del pack["fields"][2][key]
    assert f"fields[2].{key}" in _paths(validate_pack(pack))
    with pytest.raises(ExogenaPackInvalid):
        load_pack(pack)


def test_T_ACC_EXO_01_rejects_unknown_field_type_and_rule_kind() -> None:
    pack = _pack()
    pack["fields"][0]["type"] = "float"  # money is never float; also simply unknown
    pack["rules"][0]["kind"] = "magic"
    problems = validate_pack(pack)
    assert "fields[0].type" in _paths(problems)
    assert "rules[0].kind" in _paths(problems)
    with pytest.raises(ExogenaPackInvalid):
        load_pack(pack)


def test_T_ACC_EXO_01_rejects_inconsistent_definitions() -> None:
    pack = _pack()
    pack["fields"][1]["name"] = pack["fields"][0]["name"]  # duplicate
    pack["fields"][3]["length"] = 0
    pack["fields"][4]["rules"] = ["R-DOES-NOT-EXIST"]
    pack["rules"][1]["fields"] = ["numero_identificacion"]  # nit rule needs two fields
    pack["file_spec"]["format"] = "PDF"
    pack["unexpected"] = True
    paths = _paths(validate_pack(pack))
    assert {"fields[1].name", "fields[3].length", "fields[4].rules", "rules[1].fields", "file_spec.format", ""} <= paths


def test_T_ACC_EXO_01_synthetic_pack_cannot_claim_validity() -> None:
    pack = _pack()
    pack["legal_status"] = "VERIFIED_BY_USER"
    assert "legal_status" in _paths(validate_pack(pack))
    pack["legal_status"] = "SOMETHING"
    assert "legal_status" in _paths(validate_pack(pack))
    assert validate_pack("not a pack") == [{"path": "", "message": "pack must be a JSON object"}]


def test_T_ACC_EXO_01_official_import_starts_pending_verification() -> None:
    pack = _pack()
    pack.update(pack_code="IMP-TEST", source="OFFICIAL_IMPORTED", legal_status="NOT_DIAN_VALID")
    assert "legal_status" in _paths(validate_pack(pack))
    pack["legal_status"] = "PENDING_VERIFICATION"
    loaded = load_pack(pack)
    assert loaded.synthetic is False and loaded.verified is False
    assert loaded.label == "Pendiente de verificación"


@pytest.mark.django_db
def test_T_ACC_EXO_01_stored_synthetic_pack_is_not_dian_valid(seeded: Any) -> None:
    from tax.exogena.models import ExogenaFormatPack

    row = ExogenaFormatPack.objects.get(tenant_id=seeded.tenant_id, code=SYNTHETIC_PACK_CODE)
    assert row.legal_status == "NOT_DIAN_VALID"
    assert row.synthetic and not row.verified
    assert row.label == SYNTHETIC_LABEL
    assert row.checksum == load_synthetic_pack().checksum


@pytest.mark.django_db
def test_T_ACC_EXO_01_db_refuses_synthetic_pack_marked_valid(seeded: Any) -> None:
    from django.db import IntegrityError, transaction

    from tax.exogena.models import ExogenaFormatPack

    row = ExogenaFormatPack.objects.get(tenant_id=seeded.tenant_id, code=SYNTHETIC_PACK_CODE)
    with pytest.raises(IntegrityError), transaction.atomic():
        ExogenaFormatPack.objects.filter(pk=row.pk).update(legal_status="VERIFIED_BY_USER")


@pytest.mark.django_db
def test_T_ACC_EXO_01_api_rejects_invalid_pack_and_lists_synthetic(seeded: Any, api: Any, policy: Any) -> None:
    from tax.testing.fixtures import grant_all

    grant_all(policy, seeded)
    bad = _pack()
    bad["fields"][0]["type"] = "blob"
    resp = api.command("post", "/api/v1/tax/exogena/format-packs", {"code": bad["pack_code"], "year": 2026,
                                                                      "pack": bad})  # fmt: skip
    assert resp.status_code == 422
    assert resp.json()["code"] == "exogena_pack_invalid"
    listed = api.get("/api/v1/tax/exogena/format-packs").json()["items"]
    assert [(p["code"], p["synthetic"], p["verified"], p["label"]) for p in listed] == [
        (SYNTHETIC_PACK_CODE, True, False, SYNTHETIC_LABEL)
    ]


# ----------------------------------------------------------------------------- rule engine (for P17)


def _row(**over: Any) -> dict[str, Any]:
    row = {"concepto": "SYN-PAGO-SER", "tipo_documento": "NIT", "numero_identificacion": "800197268", "dv": "4",
           "razon_social": "Tercero Sintético S.A.S.", "municipio": "11001", "pago_deducible": "1500000",
           "pago_no_deducible": "0", "retencion_practicada": "60000"}  # fmt: skip
    row.update(over)
    return row


def test_P06_exogena_rules_accept_valid_rows() -> None:
    report = validate_rows(
        load_synthetic_pack(), [_row(), _row(tipo_documento="CC", numero_identificacion="1234", dv="")]
    )
    assert report.ok, report.to_json()
    assert report.to_json()["rows"] == 2


def test_P06_exogena_rules_report_row_level_errors() -> None:
    rows = [
        _row(dv="5"),  # wrong check digit
        _row(pago_deducible="-10"),  # negative amount
        _row(concepto="5001"),  # not a concept of the pack
        _row(municipio="1100"),  # not a DANE code
        _row(razon_social=""),  # required
        _row(pago_no_deducible="10.5"),  # decimals not allowed by the layout
        {**_row(), "extra": "x"},  # unknown field
        _row(pago_deducible=Decimal("1.0") * 3, retencion_practicada=1.5),  # float refused
    ]
    report = validate_rows(load_synthetic_pack(), rows)
    assert not report.ok
    got = {(e.row, e.field) for e in report.blocking}
    assert {(1, "dv"), (2, "pago_deducible"), (3, "concepto"), (4, "municipio"), (5, "razon_social"),
            (6, "pago_no_deducible"), (7, "extra"), (8, "retencion_practicada")} <= got  # fmt: skip
    assert report.to_json()["blocking_errors"] == len(report.blocking)
