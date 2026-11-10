"""F-INV-BARCODE API (contracts/openapi/modules/inventory.yaml): ``barcodes:generate`` / ``:validate``,
``barcodes/lookup``, ``label-templates`` (CRUD; DELETE archives) and ``label-print-jobs`` (+ ``/pdf``,
``/preview.png``). Same secured base, permission checks, field policy and idempotency as the P09a core."""

from __future__ import annotations

import uuid
from typing import Any

from django.http import HttpResponse
from rest_framework.request import Request
from rest_framework.response import Response

from inventory.api.base import InventoryView, body, s
from inventory.barcodes import labels
from inventory.barcodes import service as barcodes
from inventory.barcodes.models import Barcode, LabelPrintJob, LabelTemplate
from kernel import clock, ids, tenancy
from kernel.errors import ValidationFailed
from kernel.interfaces.documents import RenderedDocument
from kernel.uow import UnitOfWork


def _only(data: dict[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValidationFailed(
            f"unknown fields: {', '.join(unknown)}", pointer=f"/{unknown[0]}", reason="additional_property"
        )


def _params(request: Request) -> dict[str, Any]:
    data = body(request)
    _only(data, {"reason", "comment", "effective_date", "parameters"})
    params = data.get("parameters") or {}
    if not isinstance(params, dict):
        raise ValidationFailed("parameters must be an object", pointer="/parameters", reason="type")
    return dict(params)


def _uuid(value: Any, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise ValidationFailed(f"{field} must be a UUID", pointer=f"/parameters/{field}", reason="format") from None


def barcode_dict(b: Barcode) -> dict[str, Any]:
    return {
        "id": s(b.id),
        "tenant_id": s(b.tenant_id),
        "company_id": s(b.company_id),
        "value": b.value,
        "symbology": b.symbology,
        "owner_type": b.owner_type,
        "owner_id": s(b.owner_id),
        "item_id": s(b.item_id),
        "source": b.source,
        "gs1_prefix": b.gs1_prefix or None,
        "gs1_ownership_claimed": b.gs1_ownership_claimed,
        "label": barcodes.INTERNAL_LABEL if b.gs1_prefix and not b.gs1_ownership_claimed else None,
        "status": b.status,
        "version": b.version,
    }


class BarcodeView(InventoryView):
    perm, resource_type = "inventory.barcode", "inventory.barcode"


class BarcodeGenerateView(BarcodeView):
    """``POST barcodes:generate`` — parameters {owner_type, owner_id, symbology CODE128|EAN13, gs1_prefix?}
    or {mode: "import", owner_type, owner_id, value} to register an existing GTIN (validated, not claimed)."""

    def post(self, request: Request) -> Response:
        company = self.company(request)
        self.allow("create")
        p = _params(request)
        _only(p, {"owner_type", "owner_id", "symbology", "gs1_prefix", "mode", "value"})
        owner_type = str(p.get("owner_type") or "")
        owner_id = _uuid(p.get("owner_id"), "owner_id")
        mode = p.get("mode") or "generate"
        if mode not in ("generate", "import"):
            raise ValidationFailed("mode is generate or import", pointer="/parameters/mode", reason="enum")
        started = clock.now()

        def run(u: UnitOfWork) -> tuple[dict[str, Any], int]:
            if mode == "import":
                bc = barcodes.register_imported(u, company, owner_type, owner_id, p.get("value"))
            else:
                bc = barcodes.generate(
                    u,
                    company,
                    owner_type,
                    owner_id,
                    str(p.get("symbology") or "CODE128"),
                    str(p["gs1_prefix"]) if p.get("gs1_prefix") not in (None, "") else None,
                )
            op = {
                "id": s(ids.new_id()),
                "kind": "inventory_barcodes_generate",
                "status": "SUCCEEDED",
                "progress": "100",
                "started_at": started.isoformat(),
                "finished_at": clock.now().isoformat(),
                "result_links": [{"rel": "lookup", "href": f"/api/v1/inventory/barcodes/lookup?value={bc.value}"}],
                "result": barcode_dict(bc),
            }
            return self.serialize(op), 202

        return self.command(request, "inventory.barcode.generate", run)


class BarcodeValidateView(BarcodeView):
    """``POST barcodes:validate`` — parameters {value, symbology?, owner_type?, owner_id?}: check digit + uniqueness
    (``barcode_checkdigit_invalid`` / ``barcode_duplicate``). Read-only (``inventory.barcode.read``)."""

    def post(self, request: Request) -> Response:
        company = self.company(request)
        self.allow("read")
        p = _params(request)
        _only(p, {"value", "symbology", "owner_type", "owner_id"})
        if "value" not in p:
            raise ValidationFailed("value is required", pointer="/parameters/value", reason="required")
        with tenancy.scope(self.ctx):
            owner = None
            if p.get("owner_type") and p.get("owner_id"):
                owner = barcodes.resolve_owner(company, str(p["owner_type"]), _uuid(p["owner_id"], "owner_id"))
            result = barcodes.validate(company, p["value"], p.get("symbology"), owner)
        return Response(self.serialize({"status": "valid", "data": result}))


class BarcodeLookupView(BarcodeView):
    """``GET barcodes/lookup?value=`` — scan resolution (item/presentation/location/lot) or ``barcode_unknown``."""

    def get(self, request: Request) -> Response:
        company = self.company(request)
        self.allow("read")
        with tenancy.scope(self.ctx):
            data = barcodes.lookup(self.ctx, company, request.query_params.get("value", ""))
        resource = {"type": f"inventory.{data['owner_type']}", "id": data["owner_id"], "number": data.get("code")}
        return Response(self.serialize({"status": "resolved", "resource": resource, "data": data}))


# --------------------------------------------------------------------------- label templates


def template_dict(t: LabelTemplate) -> dict[str, Any]:
    return {
        "id": s(t.id),
        "tenant_id": s(t.tenant_id),
        "company_id": s(t.company_id),
        "code": t.code,
        "name": t.name,
        "status": t.status,
        "symbology": t.symbology,
        "layout": t.layout,
        "size_mm": f"{t.width_mm}x{t.height_mm}",
        "version": t.version,
        "audit": {"at": t.updated_at.isoformat(), "by": s(t.updated_by or t.created_by)},
    }


class LabelTemplateListView(InventoryView):
    perm, resource_type = "inventory.label_template", "inventory.label_template"

    def to_dict(self, obj: Any) -> dict[str, Any]:
        return template_dict(obj)

    def get(self, request: Request) -> Response:
        company = self.company(request)
        qs = self.scoped("read", LabelTemplate.objects.all(), company)
        return self.paginate(
            request,
            qs,
            search_fields=("code", "name"),
            filter_fields={"status": "status"},
            sort_fields=("code", "name"),
        )

    def post(self, request: Request) -> Response:
        company = self.company(request)
        self.allow("create")
        data = body(request)
        _only(data, {"code", "name", "symbology", "layout", "size_mm"})
        return self.command(
            request,
            "inventory.label_template.create",
            lambda u: (self.serialize(labels.create_template(u, company, data)), 201),
        )


class LabelTemplateDetailView(LabelTemplateListView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:  # type: ignore[override]
        t = self.one("read", LabelTemplate.objects.all(), self.company(request), pk)
        return self.etagged(self.serialize(t), t)

    def patch(self, request: Request, pk: uuid.UUID) -> Response:
        company = self.company(request)
        t = self.one("update", LabelTemplate.objects.all(), company, pk)
        self.if_match(request, t)
        data = body(request)
        _only(data, {"code", "name", "symbology", "layout", "size_mm"})
        return self.command(
            request,
            "inventory.label_template.update",
            lambda u: (self.serialize(labels.update_template(u, t, data)), 200),
        )

    def delete(self, request: Request, pk: uuid.UUID) -> Response:
        company = self.company(request)
        t = self.one("delete", LabelTemplate.objects.all(), company, pk)

        def run(u: UnitOfWork) -> tuple[Any, int]:
            labels.archive_template(u, t)
            return None, 204

        return self.command(request, "inventory.label_template.delete", run)


# --------------------------------------------------------------------------- print jobs


def job_dict(j: LabelPrintJob) -> dict[str, Any]:
    return {
        "id": s(j.id),
        "tenant_id": s(j.tenant_id),
        "company_id": s(j.company_id),
        "status": j.status,
        "template_id": s(j.template_id),
        "targets": j.targets,
        "copies": j.copies,
        "label_count": j.label_count,
        "data_hash": j.data_hash or None,
        "pdf_hash": j.pdf_hash or None,
        "version": j.version,
        "audit": {"at": j.updated_at.isoformat(), "by": s(j.updated_by or j.created_by)},
    }


class LabelPrintJobListView(InventoryView):
    perm, resource_type = "inventory.label_print_job", "inventory.label_print_job"

    def to_dict(self, obj: Any) -> dict[str, Any]:
        return job_dict(obj)

    def get(self, request: Request) -> Response:
        company = self.company(request)
        qs = self.scoped("read", LabelPrintJob.objects.all(), company)
        return self.paginate(request, qs, filter_fields={"status": "status"}, sort_fields=("created_at",))

    def post(self, request: Request) -> Response:
        company = self.company(request)
        self.allow("create")
        data = body(request)
        _only(data, {"template_id", "targets", "copies"})
        template_id = data.get("template_id")
        try:
            tid = uuid.UUID(str(template_id))
        except ValueError:
            raise ValidationFailed("template_id must be a UUID", pointer="/template_id", reason="format") from None
        copies = data.get("copies", 1)
        return self.command(
            request,
            "inventory.label_print_job.create",
            lambda u: (self.serialize(labels.create_job(u, company, tid, data.get("targets"), copies)), 201),
        )


class LabelPrintJobDetailView(LabelPrintJobListView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:  # type: ignore[override]
        j = self.one("read", LabelPrintJob.objects.all(), self.company(request), pk)
        return self.etagged(self.serialize(j), j)


class LabelPrintJobPdfView(LabelPrintJobListView):
    def get(self, request: Request, pk: uuid.UUID) -> HttpResponse:  # type: ignore[override]
        from kernel.uow import run_in_unit_of_work

        company = self.company(request)
        j = self.one("export", LabelPrintJob.objects.select_related("template"), company, pk)
        doc = run_in_unit_of_work(lambda u: labels.render_pdf(u, j), self.ctx)
        if not isinstance(doc, RenderedDocument):  # pragma: no cover - no idempotency key → never a replay
            raise ValidationFailed("unexpected render result")
        resp = HttpResponse(doc.content, content_type=doc.media_type)
        resp["Content-Disposition"] = f'attachment; filename="etiquetas-{j.id}.pdf"'
        return resp


class LabelPrintJobPreviewView(LabelPrintJobListView):
    def get(self, request: Request, pk: uuid.UUID) -> HttpResponse:  # type: ignore[override]
        company = self.company(request)
        j = self.one("export", LabelPrintJob.objects.all(), company, pk)
        return HttpResponse(labels.job_preview(j), content_type="image/png")
