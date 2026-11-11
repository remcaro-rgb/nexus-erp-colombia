"""Label templates, print jobs and rendering (F-INV-BARCODE, D-005) + the ``kernel.interfaces.documents.Labels``
implementation.

* Templates have fixed fields (``LABEL_FIELDS``); there is no cost field, so a label can never print cost.
* A print job resolves its targets (items / presentations / locations / lots), takes each owner's barcode
  and renders the label data at creation (QUEUED → RENDERED, data hash). ``…/pdf`` renders through the
  ``DocumentRenderer`` contract (P17, WeasyPrint at G3; contract double before) and records the PDF hash
  (→ DOWNLOADED; printing is the browser's print dialog, no printer driver in dev). ``…/preview.png`` is a
  deterministic pure-Python PNG of the first label's bars.
* Label jobs are audited, never journaled.
"""

from __future__ import annotations

import hashlib
import json
import struct
import uuid
import zlib
from typing import Any

from inventory.barcodes import core
from inventory.barcodes import service as barcodes
from inventory.barcodes.models import LabelPrintJob, LabelTemplate
from inventory.common import audit
from inventory.models import Location, Lot, Presentation
from kernel import services
from kernel.errors import Conflict, NotFound, ValidationFailed
from kernel.interfaces.documents import DocumentRenderer, LabelRequest, RenderedDocument
from kernel.uow import UnitOfWork, require_uow

LABEL_FIELDS = ("value", "code", "name", "presentation", "lot", "expiry_date", "location", "company")
SYMBOLOGIES = ("CODE128", "EAN13", "QR", "GS1_128")
TARGET_KEYS = {"items": "item", "presentations": "presentation", "locations": "location", "lots": "lot"}
TEMPLATE_CODE = "inventory.label"
MAX_LABELS = 2000
DEFAULT_LAYOUT = {"fields": ["value", "code", "name"]}


# --------------------------------------------------------------------------- templates


def _size(value: Any) -> tuple[int, int]:
    if value in (None, ""):
        return 50, 25
    text = str(value).lower().replace("×", "x").replace(" ", "")
    parts = text.removesuffix("mm").split("x")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ValidationFailed("size_mm is 'WIDTHxHEIGHT' in millimetres, e.g. '50x25'", pointer="/size_mm")
    w, h = int(parts[0]), int(parts[1])
    if not (10 <= w <= 300 and 10 <= h <= 300):
        raise ValidationFailed("label sizes are 10–300 mm", pointer="/size_mm", reason="range")
    return w, h


def _layout(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationFailed("layout must be an object", pointer="/layout", reason="type")
    unknown = sorted(set(value) - {"fields", "show_text", "font_size"})
    if unknown:
        raise ValidationFailed(f"unknown layout keys: {', '.join(unknown)}", pointer=f"/layout/{unknown[0]}")
    fields = value.get("fields", DEFAULT_LAYOUT["fields"])
    if not isinstance(fields, list) or not fields or not all(isinstance(f, str) for f in fields):
        raise ValidationFailed("layout.fields is a non-empty list of field names", pointer="/layout/fields")
    bad = [f for f in fields if f not in LABEL_FIELDS]
    if bad:
        # fixed fields only: anything else (e.g. cost) is refused, never printed
        raise ValidationFailed(
            f"label fields must be among {', '.join(LABEL_FIELDS)} (got {', '.join(bad)})",
            pointer="/layout/fields",
            reason="enum",
        )
    out: dict[str, Any] = {"fields": list(dict.fromkeys(fields))}
    if "show_text" in value:
        out["show_text"] = bool(value["show_text"])
    if "font_size" in value:
        size = value["font_size"]
        if not isinstance(size, int) or isinstance(size, bool) or not 4 <= size <= 24:
            raise ValidationFailed("font_size is an integer 4–24", pointer="/layout/font_size")
        out["font_size"] = size
    return out


def _symbology(value: Any) -> str:
    if value not in SYMBOLOGIES:
        raise ValidationFailed(f"symbology must be one of {', '.join(SYMBOLOGIES)}", pointer="/symbology")
    return str(value)


def _text(data: dict[str, Any], key: str, limit: int, required: bool) -> str | None:
    value = data.get(key)
    if value in (None, ""):
        if required:
            raise ValidationFailed(f"{key} is required", pointer=f"/{key}", reason="required")
        return None
    if not isinstance(value, str) or len(value.strip()) > limit:
        raise ValidationFailed(f"{key} is a string of at most {limit} characters", pointer=f"/{key}")
    return value.strip()


def create_template(uow: UnitOfWork, company_id: uuid.UUID, data: dict[str, Any]) -> LabelTemplate:
    uow.ctx.require_company(company_id)
    code = str(_text(data, "code", 40, True))
    if LabelTemplate.objects.filter(company_id=company_id, code=code).exists():
        raise Conflict(f"label template {code} already exists", code="conflict")
    w, h = _size(data.get("size_mm"))
    t = LabelTemplate.objects.create(
        tenant_id=uow.ctx.tenant_id,
        company_id=company_id,
        code=code,
        name=str(_text(data, "name", 200, True)),
        symbology=_symbology(data.get("symbology")),
        layout=_layout(data.get("layout")),
        width_mm=w,
        height_mm=h,
        created_by=uow.ctx.principal_id,
        updated_by=uow.ctx.principal_id,
    )
    audit(uow, "create", "inventory.label_template", t, {"code": t.code})
    return t


def update_template(uow: UnitOfWork, t: LabelTemplate, data: dict[str, Any]) -> LabelTemplate:
    fields: list[str] = []
    if "code" in data:
        code = _text(data, "code", 40, True)
        if LabelTemplate.objects.filter(company_id=t.company_id, code=code).exclude(id=t.id).exists():
            raise Conflict(f"label template {code} already exists", code="conflict")
        t.code = code or t.code
        fields.append("code")
    if "name" in data:
        t.name = _text(data, "name", 200, True) or t.name
        fields.append("name")
    if "symbology" in data:
        t.symbology = _symbology(data["symbology"])
        fields.append("symbology")
    if "layout" in data:
        t.layout = _layout(data["layout"])
        fields.append("layout")
    if "size_mm" in data:
        t.width_mm, t.height_mm = _size(data["size_mm"])
        fields += ["width_mm", "height_mm"]
    if not fields:
        raise ValidationFailed("nothing to update", pointer="/", reason="min_properties")
    t.save_versioned(uow.ctx.principal_id, fields)
    t.refresh_from_db()
    audit(uow, "update", "inventory.label_template", t, {"fields": fields})
    return t


def archive_template(uow: UnitOfWork, t: LabelTemplate) -> LabelTemplate:
    t.status = "ARCHIVED"
    t.save_versioned(uow.ctx.principal_id, ["status"])
    audit(uow, "archive", "inventory.label_template", t)
    return t


def default_template(uow: UnitOfWork, company_id: uuid.UUID, symbology: str = "CODE128") -> LabelTemplate:
    """The company's ``ESTANDAR-<symbology>`` 50×25 mm template (created on first use)."""
    code = f"ESTANDAR-{symbology}"
    t = LabelTemplate.objects.filter(company_id=company_id, code=code).first()
    if t is None:
        t = create_template(
            uow,
            company_id,
            {"code": code, "name": f"Etiqueta estándar 50×25 ({symbology})", "symbology": symbology,
             "layout": DEFAULT_LAYOUT, "size_mm": "50x25"},
        )  # fmt: skip
    return t


# --------------------------------------------------------------------------- print jobs


def _targets(raw: Any) -> list[tuple[str, uuid.UUID, int]]:
    if not isinstance(raw, dict) or not raw:
        raise ValidationFailed("targets is an object with items/presentations/locations/lots", pointer="/targets")
    unknown = sorted(set(raw) - set(TARGET_KEYS))
    if unknown:
        raise ValidationFailed(f"unknown target kind {unknown[0]}", pointer=f"/targets/{unknown[0]}")
    out: list[tuple[str, uuid.UUID, int]] = []
    for key, owner_type in TARGET_KEYS.items():
        entries = raw.get(key) or []
        if not isinstance(entries, list):
            raise ValidationFailed(f"targets.{key} must be a list", pointer=f"/targets/{key}")
        for n, entry in enumerate(entries):
            ptr = f"/targets/{key}/{n}"
            if isinstance(entry, str):
                entry = {"id": entry}
            if not isinstance(entry, dict):
                raise ValidationFailed("a target is an id or {id, copies}", pointer=ptr)
            try:
                oid = uuid.UUID(str(entry.get("id")))
            except ValueError:
                raise ValidationFailed("target id must be a UUID", pointer=f"{ptr}/id", reason="format") from None
            copies = entry.get("copies", 1)
            if not isinstance(copies, int) or isinstance(copies, bool) or not 1 <= copies <= 500:
                raise ValidationFailed("copies is an integer 1–500", pointer=f"{ptr}/copies", reason="range")
            out.append((owner_type, oid, copies))
    if not out:
        raise ValidationFailed("at least one target is required", pointer="/targets", reason="required")
    return out


def _label_text(company_id: uuid.UUID, owner: barcodes.Owner, value: str, company_name: str) -> dict[str, str]:
    text = {"value": value, "code": owner.code, "name": owner.name[:80], "company": company_name}
    if owner.owner_type == "presentation":
        p = Presentation.objects.filter(id=owner.owner_id).first()
        if p is not None:
            text["presentation"] = f"{p.name} ({format(p.factor.normalize(), 'f')})"
    elif owner.owner_type == "lot":
        lot = Lot.objects.filter(id=owner.owner_id).first()
        if lot is not None:
            text["lot"] = lot.code
            if lot.expiry_date:
                text["expiry_date"] = lot.expiry_date.isoformat()
    elif owner.owner_type == "location":
        loc = Location.objects.filter(id=owner.owner_id).first()
        if loc is not None:
            text["location"] = loc.path or loc.code
    if value.isdigit() and core.is_restricted_circulation(value.zfill(13)[-13:]) and len(value) == 13:
        text["notice"] = barcodes.INTERNAL_LABEL
    return text


def _canonical(data: Any) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def render_labels(
    company_id: uuid.UUID, template: LabelTemplate, targets: list[tuple[str, uuid.UUID, int]], copies: int
) -> list[dict[str, Any]]:
    from iam.models import Company

    company_name = Company.objects.filter(id=company_id).values_list("legal_name", flat=True).first() or ""
    fields = template.layout.get("fields", DEFAULT_LAYOUT["fields"])
    labels: list[dict[str, Any]] = []
    for n, (owner_type, oid, target_copies) in enumerate(targets):
        owner = barcodes.resolve_owner(company_id, owner_type, oid)
        primary = barcodes.primary_value(company_id, owner)
        if primary is None:
            raise ValidationFailed(
                f"{owner_type} {owner.code} has no barcode: generate one first",
                pointer=f"/targets/{n}",
                reason="no_barcode",
            )
        value, symbology = primary
        # EAN-13 values print as EAN-13; everything else as Code 128 (QR/GS1-128 fall back to Code 128 in dev)
        sym = core.symbol("EAN13" if symbology == "EAN13" and len(value) == 13 else "CODE128", value)
        text = _label_text(company_id, owner, value, company_name)
        shown = {k: v for k, v in text.items() if k in fields or k == "notice"}
        for _ in range(target_copies * copies):
            labels.append(
                {"owner_type": owner_type, "owner_id": str(oid), "value": value, "symbology": sym.symbology,
                 "modules": sym.modules, "text": shown}
            )  # fmt: skip
            if len(labels) > MAX_LABELS:
                raise ValidationFailed(
                    f"a print job holds at most {MAX_LABELS} labels", pointer="/copies", reason="range"
                )
    return labels


def create_job(
    uow: UnitOfWork,
    company_id: uuid.UUID,
    template_id: uuid.UUID,
    targets_raw: Any,
    copies: int = 1,
    source: tuple[str, uuid.UUID] | None = None,
) -> LabelPrintJob:
    uow.ctx.require_company(company_id)
    template = LabelTemplate.objects.filter(company_id=company_id, id=template_id).first()
    if template is None:
        raise ValidationFailed("unknown label template", pointer="/template_id", reason="not_found")
    if template.status != "ACTIVE":
        raise ValidationFailed("the label template is archived", pointer="/template_id", reason="archived")
    if not isinstance(copies, int) or isinstance(copies, bool) or not 1 <= copies <= 500:
        raise ValidationFailed("copies is an integer 1–500", pointer="/copies", reason="range")
    targets = _targets(targets_raw)
    job = LabelPrintJob.objects.create(
        tenant_id=uow.ctx.tenant_id,
        company_id=company_id,
        template=template,
        targets=targets_raw,
        copies=copies,
        source_type=source[0] if source else "",
        source_id=source[1] if source else None,
        created_by=uow.ctx.principal_id,
        updated_by=uow.ctx.principal_id,
    )
    labels = render_labels(company_id, template, targets, copies)
    job.labels = labels
    job.label_count = len(labels)
    job.data_hash = hashlib.sha256(_canonical(labels)).hexdigest()
    job.status = LabelPrintJob.JobStatus.RENDERED
    job.save(update_fields=["labels", "label_count", "data_hash", "status"])
    audit(uow, "create", "inventory.label_print_job", job, {"labels": job.label_count, "data_hash": job.data_hash})
    return job


def render_pdf(uow: UnitOfWork, job: LabelPrintJob) -> RenderedDocument:
    """PDF through the DocumentRenderer contract; records the PDF hash (→ DOWNLOADED); audited."""
    t = job.template
    data = {
        "job_id": str(job.id),
        "template": {"code": t.code, "name": t.name, "width_mm": t.width_mm, "height_mm": t.height_mm,
                     "fields": t.layout.get("fields", [])},
        "labels": job.labels,
        "data_hash": job.data_hash,
    }  # fmt: skip
    doc = services.get(DocumentRenderer).render(TEMPLATE_CODE, data, media_type="application/pdf")
    job.pdf_hash = hashlib.sha256(doc.content).hexdigest()
    job.status = LabelPrintJob.JobStatus.DOWNLOADED
    job.save(update_fields=["pdf_hash", "status"])
    audit(uow, "download", "inventory.label_print_job", job, {"pdf_hash": job.pdf_hash})
    return doc


# --------------------------------------------------------------------------- PNG preview (pure Python)


def _png(width: int, height: int, rows: list[bytes]) -> bytes:
    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + r for r in rows)
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)  # 8-bit grayscale
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def preview_png(modules: str, scale: int = 2, bar_height: int = 60, quiet: int = 10) -> bytes:
    """Deterministic PNG of a module string (black bars on white, quiet zones both sides)."""
    padded = "0" * quiet + modules + "0" * quiet
    row = b"".join((b"\x00" if m == "1" else b"\xff") * scale for m in padded)
    blank = b"\xff" * len(row)
    margin = 8
    rows = [blank] * margin + [row] * bar_height + [blank] * margin
    return _png(len(row), len(rows), rows)


def job_preview(job: LabelPrintJob) -> bytes:
    if not job.labels:
        raise NotFound("the print job has no labels")
    return preview_png(job.labels[0]["modules"])


# --------------------------------------------------------------------------- kernel.interfaces.documents.Labels


class InventoryLabels:
    """``Labels`` implementation (P11 receipt label requests). Runs inside the caller's unit of work."""

    def request_labels(self, company_id: uuid.UUID, requests: list[LabelRequest]) -> uuid.UUID:
        uow = require_uow()
        if not requests or any(r.copies < 1 for r in requests):
            raise ValidationFailed("label requests need at least one copy", pointer="/requests")
        symbology = requests[0].symbology
        template = default_template(uow, company_id, symbology)
        targets = {"items": [{"id": str(r.item_id), "copies": r.copies} for r in requests]}
        src = requests[0].source
        job = create_job(uow, company_id, template.id, targets, 1, (src.type, src.id) if src else None)
        return job.id

    def render_job(self, company_id: uuid.UUID, job_id: uuid.UUID) -> RenderedDocument:
        uow = require_uow()
        job = LabelPrintJob.objects.select_related("template").filter(company_id=company_id, id=job_id).first()
        if job is None:
            raise NotFound("label print job not found")
        return render_pdf(uow, job)


SERVICE = InventoryLabels()
