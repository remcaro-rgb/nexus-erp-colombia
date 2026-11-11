"""Barcode generation, validation, import and scan lookup (F-INV-BARCODE, D-004).

* Internal codes: Code 128, value ``<company_code>-<8-digit sequence>`` from ``kernel.sequences`` code
  ``BARCODE`` (row lock → unique under concurrency; the unique index on ``(tenant, company, value)`` is the
  second line of defence).
* EAN-13 only from a GS1 company prefix entered by a user (request ``gs1_prefix`` or the stored company
  prefix); none → ``gs1_prefix_missing``. Restricted-circulation prefixes 20–29 are internal use and never
  claim GS1 ownership ('uso interno, no licencia GS1').
* Imported GTINs are validated (mod-10) and stored with ``source=IMPORTED`` and ``gs1_ownership_claimed=False``.
* Uniqueness per company spans the barcode table and the barcodes stored on P09a masters
  (``Presentation.barcode``, ``Location.barcode``, ``Item.gtin``).
* Lookup (scan) resolves item / presentation / location / lot; anything unknown or outside the caller's
  scope → ``barcode_unknown`` (no existence leak). Scans never post.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from django.db.models import Q

from iam.models import Company
from inventory.barcodes import core
from inventory.barcodes.models import Barcode, Gs1Prefix
from inventory.common import audit
from inventory.errors import error
from inventory.models import Item, Location, Lot, Presentation
from kernel import sequences
from kernel.errors import NotFound, ValidationFailed
from kernel.models import Sequence
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork

SEQUENCE_CODE = "BARCODE"
INTERNAL_LABEL = "uso interno, no licencia GS1"
OWNER_TYPES = tuple(Barcode.OwnerType.values)
MAX_COLLISION_RETRIES = 20


class BarcodeUnknown(NotFound):
    code, title = "barcode_unknown", "Barcode unknown"


@dataclass(frozen=True, slots=True)
class Owner:
    owner_type: str
    owner_id: uuid.UUID
    item_id: uuid.UUID | None
    code: str
    name: str


def normalize(value: Any) -> str:
    """Scanner input (keyboard wedge) → value: strip CR/LF/TAB and surrounding spaces."""
    if not isinstance(value, str):
        raise ValidationFailed("value must be a string", pointer="/value", reason="type")
    out = value.strip().strip("\r\n\t").strip()
    if not out or len(out) > 80:
        raise ValidationFailed("value must have 1–80 characters", pointer="/value", reason="length")
    return out


def _variants(value: str) -> list[str]:
    """Equivalent spellings of a numeric GTIN (UPC-A 12 ≡ EAN-13 with leading 0 ≡ GTIN-14)."""
    out = [value]
    if value.isdigit() and len(value) in core.GTIN_LENGTHS:
        try:
            g14 = core.gtin14(value)
        except core.BarcodeFormatError:
            return out
        for n in (14, 13, 12, 8):
            v = g14[14 - n :]
            if v.zfill(14) == g14 and v not in out:
                out.append(v)
    return out


# --------------------------------------------------------------------------- owners


def resolve_owner(company_id: uuid.UUID, owner_type: str, owner_id: uuid.UUID) -> Owner:
    if owner_type not in OWNER_TYPES:
        raise ValidationFailed(
            f"owner_type must be one of {', '.join(OWNER_TYPES)}", pointer="/owner_type", reason="enum"
        )
    if owner_type == "item":
        item = Item.objects.filter(company_id=company_id, id=owner_id).first()
        if item is None:
            raise NotFound("item not found")
        return Owner(owner_type, item.id, item.id, item.code, item.name)
    if owner_type == "presentation":
        p = Presentation.objects.select_related("item").filter(company_id=company_id, id=owner_id).first()
        if p is None:
            raise NotFound("presentation not found")
        return Owner(owner_type, p.id, p.item_id, f"{p.item.code}/{p.code}", f"{p.item.name} — {p.name}")
    if owner_type == "location":
        loc = Location.objects.select_related("warehouse").filter(company_id=company_id, id=owner_id).first()
        if loc is None:
            raise NotFound("location not found")
        return Owner(owner_type, loc.id, None, f"{loc.warehouse.code}/{loc.path or loc.code}", loc.name)
    lot = Lot.objects.select_related("item").filter(company_id=company_id, id=owner_id).first()
    if lot is None:
        raise NotFound("lot not found")
    return Owner(owner_type, lot.id, lot.item_id, f"{lot.item.code}/{lot.code}", lot.item.name)


def _holder(company_id: uuid.UUID, value: str) -> tuple[str, uuid.UUID] | None:
    """(owner_type, owner_id) already using ``value`` in this company (any store), else None."""
    variants = _variants(value)
    bc = Barcode.objects.filter(company_id=company_id, value__in=variants).first()
    if bc is not None:
        return bc.owner_type, bc.owner_id
    p = Presentation.objects.filter(company_id=company_id, barcode__in=variants).first()
    if p is not None:
        return "presentation", p.id
    loc = Location.objects.filter(company_id=company_id, barcode__in=variants).first()
    if loc is not None:
        return "location", loc.id
    item = Item.objects.filter(company_id=company_id, gtin__in=variants).first()
    if item is not None:
        return "item", item.id
    return None


def assert_free(company_id: uuid.UUID, value: str, owner: Owner | None = None) -> None:
    held = _holder(company_id, value)
    if held is None:
        return
    if owner is not None and held == (owner.owner_type, owner.owner_id):
        return
    raise error("barcode_duplicate", f"barcode {value} is already used by a {held[0]} of this company")


def _company_code(company_id: uuid.UUID) -> str:
    company = Company.objects.filter(id=company_id).first()
    if company is None:
        raise NotFound("company not found")
    code = "".join(ch for ch in company.code.upper() if ch.isalnum()) or "CO"
    return code[:20]


def _ensure_sequence(uow: UnitOfWork, company_id: uuid.UUID) -> None:
    Sequence.objects.bulk_create(
        [
            Sequence(
                tenant_id=uow.ctx.tenant_id, company_id=company_id, sequence_code=SEQUENCE_CODE, prefix="", padding=8
            )
        ],
        ignore_conflicts=True,
    )


# --------------------------------------------------------------------------- generation


def _create(
    uow: UnitOfWork,
    company_id: uuid.UUID,
    owner: Owner,
    value: str,
    symbology: str,
    source: str,
    gs1_prefix: str = "",
    claimed: bool = False,
) -> Barcode:
    bc = Barcode.objects.create(
        tenant_id=uow.ctx.tenant_id,
        company_id=company_id,
        value=value,
        symbology=symbology,
        owner_type=owner.owner_type,
        owner_id=owner.owner_id,
        item_id=owner.item_id,
        source=source,
        gs1_prefix=gs1_prefix,
        gs1_ownership_claimed=claimed,
        created_by=uow.ctx.principal_id,
        updated_by=uow.ctx.principal_id,
    )
    uow.emit(
        "inventory.barcode.generated.v1",
        {
            "value": value,
            "symbology": symbology,
            "owner": {"type": f"inventory.{owner.owner_type}", "id": str(owner.owner_id), "number": owner.code[:60]},
        },
        company_id=company_id,
    )
    audit(uow, "generate" if source != Barcode.Source.IMPORTED else "import", "inventory.barcode", bc, {"value": value})
    return bc


def _gs1_row(uow: UnitOfWork, company_id: uuid.UUID, entered: str | None) -> Gs1Prefix:
    if entered:
        prefix = entered.strip()
        if not prefix.isdigit() or not 2 <= len(prefix) <= 11:
            raise ValidationFailed("the GS1 company prefix has 2–11 digits", pointer="/gs1_prefix", reason="format")
        Gs1Prefix.objects.bulk_create(
            [
                Gs1Prefix(
                    tenant_id=uow.ctx.tenant_id,
                    company_id=company_id,
                    prefix=prefix,
                    restricted_circulation=core.is_restricted_circulation(prefix),
                    created_by=uow.ctx.principal_id,
                    updated_by=uow.ctx.principal_id,
                )
            ],
            ignore_conflicts=True,
        )
        row = Gs1Prefix.objects.select_for_update().filter(company_id=company_id, prefix=prefix).first()
    else:
        row = Gs1Prefix.objects.select_for_update().filter(company_id=company_id).order_by("created_at", "id").first()
    if row is None:
        raise error(
            "gs1_prefix_missing",
            "EAN-13 needs the company GS1 prefix entered by a user (D-004); use Code 128 for internal codes",
        )
    return row


def generate(
    uow: UnitOfWork,
    company_id: uuid.UUID,
    owner_type: str,
    owner_id: uuid.UUID,
    symbology: str = "CODE128",
    gs1_prefix: str | None = None,
) -> Barcode:
    """``barcodes:generate`` — a new internal Code 128 or a GS1-prefix EAN-13 for the owner."""
    uow.ctx.require_company(company_id)
    owner = resolve_owner(company_id, owner_type, owner_id)
    if symbology == "CODE128":
        _ensure_sequence(uow, company_id)
        code = _company_code(company_id)
        for _ in range(MAX_COLLISION_RETRIES):
            n = sequences.allocate(company_id, SEQUENCE_CODE, "").number
            value = f"{code}-{n:08d}"
            if _holder(company_id, value) is None:
                return _create(uow, company_id, owner, value, "CODE128", Barcode.Source.GENERATED_INTERNAL)
        raise error("barcode_duplicate", "no free internal code after repeated collisions")
    if symbology == "EAN13":
        row = _gs1_row(uow, company_id, gs1_prefix)
        for _ in range(MAX_COLLISION_RETRIES):
            try:
                value = core.ean13_from_prefix(row.prefix, row.next_reference)
            except core.BarcodeFormatError as exc:
                raise ValidationFailed(str(exc), pointer="/gs1_prefix", reason=exc.reason) from None
            row.next_reference += 1
            row.save(update_fields=["next_reference"])
            if _holder(company_id, value) is None:
                return _create(
                    uow,
                    company_id,
                    owner,
                    value,
                    "EAN13",
                    Barcode.Source.GENERATED_GS1_PREFIX,
                    gs1_prefix=row.prefix,
                    claimed=not row.restricted_circulation,
                )
        raise error("barcode_duplicate", "no free EAN-13 reference after repeated collisions")
    raise ValidationFailed("symbology must be CODE128 or EAN13", pointer="/symbology", reason="enum")


def register_imported(
    uow: UnitOfWork, company_id: uuid.UUID, owner_type: str, owner_id: uuid.UUID, value: Any
) -> Barcode:
    """Import an existing GTIN: validated (mod-10), stored as IMPORTED, ownership never claimed."""
    uow.ctx.require_company(company_id)
    owner = resolve_owner(company_id, owner_type, owner_id)
    value = normalize(value)
    try:
        gtin = core.validate_gtin(value)
    except core.BarcodeFormatError as exc:
        raise error("barcode_checkdigit_invalid", str(exc), reason=exc.reason) from None
    assert_free(company_id, gtin)
    symbology = "EAN13" if len(gtin) == 13 else "GTIN14_IMPORTED"
    return _create(uow, company_id, owner, gtin, symbology, Barcode.Source.IMPORTED)


# --------------------------------------------------------------------------- validation / lookup


def validate(
    company_id: uuid.UUID, value: str, symbology: str | None = None, owner: Owner | None = None
) -> dict[str, Any]:
    """Check digit (numeric GTINs / EAN-13) and uniqueness; errors are raised with their stable codes."""
    value = normalize(value)
    numeric = value.isdigit() and len(value) in core.GTIN_LENGTHS
    if symbology == "EAN13" or (symbology in (None, "GTIN14_IMPORTED") and numeric):
        try:
            core.validate_gtin(value)
        except core.BarcodeFormatError as exc:
            raise error("barcode_checkdigit_invalid", str(exc), reason=exc.reason) from None
        if symbology == "EAN13" and len(value) != 13:
            raise error("barcode_checkdigit_invalid", "EAN-13 needs 13 digits", reason="format")
        kind = "EAN13" if len(value) == 13 else "GTIN"
    else:
        try:
            core.code128_values(value)
        except core.BarcodeFormatError as exc:
            raise ValidationFailed(str(exc), pointer="/value", reason=exc.reason) from None
        kind = "CODE128"
    assert_free(company_id, value, owner)
    return {
        "value": value,
        "symbology": kind,
        "check_digit_valid": True,
        "unique": True,
        "restricted_circulation": numeric and core.is_restricted_circulation(value.zfill(13)[-13:]),
    }


def _scoped(ctx: TenantContext, action: str, qs: Any) -> Any:
    from inventory.api.base import policy

    return policy().scope_queryset(ctx, action, qs)


def lookup(ctx: TenantContext, company_id: uuid.UUID, raw: Any) -> dict[str, Any]:
    """Scan resolution → {owner_type, owner_id, item_id, presentation_id, location_id, lot_id, …}."""
    value = normalize(raw)
    variants = _variants(value)
    data: dict[str, Any] | None = None
    bc = Barcode.objects.filter(company_id=company_id, value__in=variants, status="ACTIVE").first()
    if bc is not None:
        data = _resolve(ctx, company_id, bc.owner_type, bc.owner_id)
        if data is not None:
            data.update(symbology=bc.symbology, source=bc.source, gs1_ownership_claimed=bc.gs1_ownership_claimed)
    if data is None:
        p = Presentation.objects.filter(company_id=company_id, barcode__in=variants).first()
        loc = Location.objects.filter(company_id=company_id, barcode__in=variants).first()
        item = Item.objects.filter(company_id=company_id, gtin__in=variants).first()
        for owner_type, obj in (("presentation", p), ("location", loc), ("item", item)):
            if obj is not None:
                data = _resolve(ctx, company_id, owner_type, obj.id)
                if data is not None:
                    data.update(symbology="EAN13" if value.isdigit() and len(value) == 13 else "CODE128")
                    data.update(source="MASTER")
                    break
    if data is None:
        raise BarcodeUnknown(f"barcode {value} is not registered in this company")
    data["value"] = value
    return data


def _resolve(ctx: TenantContext, company_id: uuid.UUID, owner_type: str, owner_id: uuid.UUID) -> dict[str, Any] | None:
    """Owner details, or None when it does not exist / is outside the caller's row scope."""
    out: dict[str, Any] = {
        "owner_type": owner_type,
        "owner_id": str(owner_id),
        "item_id": None,
        "presentation_id": None,
        "location_id": None,
        "warehouse_id": None,
        "lot_id": None,
    }
    if owner_type == "item":
        item = _scoped(ctx, "inventory.item.read", Item.objects.filter(company_id=company_id, id=owner_id)).first()
        if item is None:
            return None
        out.update(item_id=str(item.id), code=item.code, name=item.name)
    elif owner_type == "presentation":
        qs = Presentation.objects.filter(company_id=company_id, id=owner_id).select_related("item")
        p = _scoped(ctx, "inventory.presentation.read", qs).first()
        if p is None:
            return None
        out.update(
            item_id=str(p.item_id),
            presentation_id=str(p.id),
            code=p.item.code,
            name=f"{p.item.name} — {p.name}",
            presentation_code=p.code,
            factor=format(p.factor, "f"),
        )
    elif owner_type == "location":
        loc_qs = Location.objects.filter(company_id=company_id, id=owner_id)
        loc = _scoped(ctx, "inventory.location.read", loc_qs).first()
        if loc is None:
            return None
        out.update(
            location_id=str(loc.id),
            warehouse_id=str(loc.warehouse_id),
            code=loc.code,
            name=loc.name,
            location_type=loc.location_type,
        )
    else:
        lot = _scoped(
            ctx, "inventory.lot.read", Lot.objects.filter(company_id=company_id, id=owner_id).select_related("item")
        ).first()
        if lot is None:
            return None
        out.update(
            item_id=str(lot.item_id),
            lot_id=str(lot.id),
            code=lot.code,
            name=lot.item.name,
            expiry_date=lot.expiry_date.isoformat() if lot.expiry_date else None,
        )
    return out


def barcodes_of(company_id: uuid.UUID, owner_type: str, owner_id: uuid.UUID) -> list[Barcode]:
    return list(
        Barcode.objects.filter(
            company_id=company_id, owner_type=owner_type, owner_id=owner_id, status="ACTIVE"
        ).order_by("created_at", "id")
    )


def primary_value(company_id: uuid.UUID, owner: Owner) -> tuple[str, str] | None:
    """(value, symbology) printed on a label: the first registered barcode, else the master's own barcode."""
    rows = barcodes_of(company_id, owner.owner_type, owner.owner_id)
    if rows:
        return rows[0].value, rows[0].symbology
    master: str | None = None
    if owner.owner_type == "presentation":
        master = Presentation.objects.filter(id=owner.owner_id).values_list("barcode", flat=True).first()
    elif owner.owner_type == "location":
        master = Location.objects.filter(id=owner.owner_id).values_list("barcode", flat=True).first()
    elif owner.owner_type == "item":
        master = Item.objects.filter(id=owner.owner_id).values_list("gtin", flat=True).first()
    if not master:
        return None
    return master, ("EAN13" if master.isdigit() and len(master) == 13 else "CODE128")


def products_without_code(company_id: uuid.UUID) -> list[Item]:
    """Report 'Productos sin código': active stock items with no barcode and no GTIN (item or presentation)."""
    with_code = set(
        Barcode.objects.filter(company_id=company_id, item_id__isnull=False).values_list("item_id", flat=True)
    ) | set(Presentation.objects.filter(company_id=company_id, barcode__isnull=False).values_list("item_id", flat=True))
    return list(
        Item.objects.filter(company_id=company_id, status="ACTIVE")
        .filter(Q(gtin__isnull=True) | Q(gtin=""))
        .exclude(id__in=with_code)
        .order_by("code")
    )
