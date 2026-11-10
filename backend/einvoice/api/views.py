"""einvoice endpoints (contract ``contracts/openapi/modules/einvoice.yaml``)."""

from __future__ import annotations

import csv
import io
import re
import uuid
from typing import Any

from django.db.models import Q
from django.http import HttpResponse
from rest_framework.request import Request
from rest_framework.response import Response

from einvoice import annex, habilitation, kpis, lifecycle, service
from einvoice import secrets as sec
from einvoice.adapters import registry
from einvoice.api import base, serializers
from einvoice.api.base import EinvoiceAPIView
from einvoice.errors import AdapterNotSupported, ProviderNotConfigured, present
from einvoice.models import (
    EinvoiceSettings,
    FiscalDocument,
    FiscalEvidence,
    HabilitationTestSet,
    NumberingRange,
    RadianEvent,
    SigningCertificate,
    TechnologyProvider,
)
from kernel import clock
from kernel.errors import Conflict, ValidationFailed
from kernel.interfaces.common import DocumentRef
from kernel.uow import UnitOfWork

SECRET_REF_FIELDS = ("credentials_ref", "technical_key_ref", "software_pin_ref")


def _operation(kind: str, ok: bool, href: str, started: str, error: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "kind": kind,
        "status": "SUCCEEDED" if ok else "FAILED",
        "progress": "100",
        "started_at": started,
        "finished_at": clock.now().isoformat(),
        "result_links": [{"rel": "result", "href": href}],
        "error": error,
    }


# ============================================================================ documents


def _documents(ctx: Any, company: uuid.UUID, permission: str = "einvoice.document.read") -> Any:
    qs = FiscalDocument.objects.filter(tenant_id=ctx.tenant_id, company_id=company)
    return base.scoped(ctx, permission, qs)


class DocumentList(EinvoiceAPIView):
    def get(self, request: Request) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "einvoice.document.read")
        qs = _documents(ctx, base.company_id(request, ctx))
        q = request.query_params
        state = q.get("status") or q.get("estado")
        if state:
            if state == "validado":
                qs = qs.filter(status__in=[FiscalDocument.State.ACCEPTED, FiscalDocument.State.ACCEPTED_WITH_NOTICES])
            elif state in FiscalDocument.State.values:
                qs = qs.filter(status=state)
            else:
                raise base.FilterInvalid(f"unknown status {state!r}")
        if q.get("fiscal_status"):
            states = [s for s, f in lifecycle.FISCAL_STATUS.items() if f == q["fiscal_status"]]
            if not states:
                raise base.FilterInvalid("unknown fiscal_status")
            qs = qs.filter(status__in=states)
        if q.get("document_type"):
            qs = qs.filter(family=q["document_type"])
        if q.get("period"):
            start, end = kpis.period_bounds(q["period"])
            qs = qs.filter(issue_date__gte=start, issue_date__lt=end)
        if q.get("q"):
            qs = qs.filter(Q(number__icontains=q["q"]) | Q(unique_code__startswith=q["q"].lower()))
        return base.page(request, qs, serializers.fiscal_document)


class DocumentDetail(EinvoiceAPIView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "einvoice.document.read")
        doc = base.get_or_404(_documents(ctx, base.company_id(request, ctx)), pk)
        return base.with_etag(Response(serializers.fiscal_document(doc)), doc)


class DocumentRetry(EinvoiceAPIView):
    def post(self, request: Request, pk: uuid.UUID) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "einvoice.document.update")
        company = base.company_id(request, ctx)
        base.action_body(request)
        doc = base.get_or_404(_documents(ctx, company, "einvoice.document.update"), pk)
        if request.headers.get("If-Match"):
            doc.check_if_match(request.headers["If-Match"])

        def fn(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            out = service.retry(uow, doc)
            return serializers.fiscal_document(out), 200, {"ETag": out.etag}

        return base.command(request, ctx, f"einvoice.document.retry:{pk}", fn)


class DocumentResend(EinvoiceAPIView):
    def post(self, request: Request, pk: uuid.UUID) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "einvoice.document.update")
        company = base.company_id(request, ctx)
        base.action_body(request)
        doc = base.get_or_404(_documents(ctx, company, "einvoice.document.update"), pk)

        def fn(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            service.resend_email(uow, doc)
            return serializers.fiscal_document(doc), 200, {"ETag": doc.etag}

        return base.command(request, ctx, f"einvoice.document.resend:{pk}", fn)


class DocumentEvents(EinvoiceAPIView):
    """Lifecycle, transmissions (redacted), deliveries and RADIAN events of one document (newest last)."""

    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "einvoice.document.read")
        doc = base.get_or_404(_documents(ctx, base.company_id(request, ctx)), pk)
        items: list[dict[str, Any]] = []
        for t in doc.transitions.all():
            items.append(
                {
                    "kind": "transition",
                    "at": t.at.isoformat(),
                    "from_status": t.from_state,
                    "to_status": t.to_state,
                    "attempt": t.attempt,
                    "detail": sec.redact(t.reason),
                }
            )
        for tx in doc.transmissions.order_by("at", "id"):
            items.append(
                {
                    "kind": "transmission",
                    "at": tx.at.isoformat(),
                    "adapter": tx.adapter_key,
                    "provider": tx.provider_code,
                    "attempt": tx.attempt,
                    "outcome": tx.outcome,
                    "latency_ms": tx.latency_ms,
                    "detail": sec.redact(tx.error),
                }
            )
        for d in doc.deliveries.order_by("at", "id"):
            items.append(
                {
                    "kind": "delivery",
                    "at": d.at.isoformat(),
                    "status": d.status,
                    "recipient": d.recipient_masked,
                    "detail": d.detail,
                }
            )
        for ev in RadianEvent.objects.filter(fiscal_document=doc).order_by("occurred_at", "id"):
            items.append(
                {
                    "kind": "radian",
                    "at": ev.occurred_at.isoformat(),
                    "event_code": ev.event_code,
                    "direction": ev.direction,
                    "status": ev.status,
                }
            )
        items.sort(key=lambda i: i["at"])
        summary = {
            "kind": "summary",
            "at": doc.updated_at.isoformat(),
            "messages": [sec.redact(m) for m in doc.messages],
            "error_codes": doc.error_codes,
            "last_error": sec.redact(doc.last_error),
            "attempts": doc.attempts,
            "simulated": doc.simulated,
            "label": "SIMULADO — NO CERTIFICADO" if doc.simulated else "",
        }
        return Response({"items": [*items, summary], "next_cursor": None})


class DocumentXml(EinvoiceAPIView):
    def get(self, request: Request, pk: uuid.UUID) -> HttpResponse:
        ctx = base.context(request)
        base.authorize(ctx, "einvoice.document.export")
        doc = base.get_or_404(_documents(ctx, base.company_id(request, ctx), "einvoice.document.export"), pk)
        ev = service.latest_evidence(doc, FiscalEvidence.Kind.SIGNED_XML) or service.latest_evidence(
            doc, FiscalEvidence.Kind.UNSIGNED_XML
        )
        if ev is None:
            raise Conflict("no XML available yet", code="validation_failed")
        resp = HttpResponse(service.read_evidence(ev), content_type="application/xml")
        resp["Content-Disposition"] = f'attachment; filename="{re.sub(r"[^A-Za-z0-9_-]", "_", doc.number)}.xml"'
        return resp


# ============================================================================ numbering ranges

RANGE_FIELDS = ("code", "prefix", "from_number", "to_number", "resolution", "valid_to", "technical_key_ref")


def _range_family(code: str) -> str:
    """The contract has no ``family`` field (CCR P15-3): ``DS-…`` → documento soporte, ``POS-…`` → DEE POS."""
    upper = code.upper()
    if upper.startswith("DS"):
        return "DS"
    if upper.startswith(("POS", "DEE")):
        return "DEE_POS"
    return "FE"


def _range_values(data: dict[str, Any], partial: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in RANGE_FIELDS:
        if key not in data:
            continue
        value = data[key]
        if key in ("from_number", "to_number"):
            value = base.parse_int(value, key)
            if value < 0:
                raise ValidationFailed(f"{key} must be ≥ 0", field=key)
        elif key == "valid_to":
            value = base.parse_date(value, key)
        elif key == "technical_key_ref":
            value = sec.validate_ref(value) if value else ""
        else:
            value = str(value).strip()
            if key == "code" and not 1 <= len(value) <= 40:
                raise ValidationFailed("code must have 1..40 characters", field="code")
            if key == "prefix" and (len(value) > 10 or not re.fullmatch(r"[A-Za-z0-9]*", value)):
                raise ValidationFailed("prefix must be ≤ 10 letters/digits", field="prefix")
        out[key] = value
    return out


class RangeList(EinvoiceAPIView):
    def get(self, request: Request) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "einvoice.numbering_range.read")
        qs = base.scoped(
            ctx,
            "einvoice.numbering_range.read",
            NumberingRange.objects.filter(tenant_id=ctx.tenant_id, company_id=base.company_id(request, ctx)),
        )
        return base.page(request, qs, serializers.numbering_range)

    def post(self, request: Request) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "einvoice.numbering_range.create")
        company = base.company_id(request, ctx)
        data = base.body_object(
            request, RANGE_FIELDS, ("code", "prefix", "from_number", "to_number", "resolution", "valid_to")
        )
        values = _range_values(data, partial=False)
        if values["from_number"] > values["to_number"]:
            raise ValidationFailed("from_number must be ≤ to_number", field="from_number")

        def fn(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            if NumberingRange.objects.filter(tenant_id=ctx.tenant_id, company_id=company, code=values["code"]).exists():
                raise Conflict("a numbering range with this code exists", code="duplicate")
            rng = NumberingRange.objects.create(
                tenant_id=ctx.tenant_id,
                company_id=company,
                family=_range_family(values["code"]),
                created_by=ctx.principal_id,
                updated_by=ctx.principal_id,
                **values,
            )
            service._audit(
                uow,
                "einvoice.numbering_range.created",
                "einvoice.numbering_range",
                rng.id,
                company,
                {"code": rng.code, "prefix": rng.prefix},
            )
            return serializers.numbering_range(rng), 201, {"ETag": rng.etag}

        return base.command(request, ctx, "einvoice.numbering_range.create", fn)


class RangeDetail(EinvoiceAPIView):
    def _get(self, request: Request, permission: str) -> tuple[Any, uuid.UUID, NumberingRange]:
        ctx = base.context(request)
        base.authorize(ctx, permission)
        company = base.company_id(request, ctx)
        qs = base.scoped(ctx, permission, NumberingRange.objects.filter(tenant_id=ctx.tenant_id, company_id=company))
        return ctx, company, base.get_or_404(qs, self.kwargs["pk"])

    def get(self, request: Request, pk: uuid.UUID) -> Response:
        _, _, rng = self._get(request, "einvoice.numbering_range.read")
        return base.with_etag(Response(serializers.numbering_range(rng)), rng)

    def patch(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, _company, rng = self._get(request, "einvoice.numbering_range.update")
        rng.check_if_match(request.headers.get("If-Match"))
        values = _range_values(base.body_object(request, RANGE_FIELDS), partial=True)
        if not values:
            raise ValidationFailed("empty patch")
        if rng.last_used_number is not None and {"prefix", "from_number", "code"} & set(values):
            raise Conflict(
                "a range in use keeps its code, prefix and start (numbers are never reused)", code="validation_failed"
            )

        def fn(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            obj = NumberingRange.objects.select_for_update().get(pk=rng.pk)
            for k, v in values.items():
                setattr(obj, k, v)
            if obj.from_number > obj.to_number or (obj.last_used_number or 0) > obj.to_number:
                raise ValidationFailed("the range would not cover the numbers already used", field="to_number")
            obj.save_versioned(ctx.principal_id, list(values))
            return serializers.numbering_range(obj), 200, {"ETag": obj.etag}

        return base.command(request, ctx, f"einvoice.numbering_range.update:{pk}", fn)

    def delete(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, _company, rng = self._get(request, "einvoice.numbering_range.delete")
        if rng.last_used_number is not None:
            raise Conflict(
                "a range already used cannot be deleted (archive it by letting it expire)", code="validation_failed"
            )

        def fn(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            NumberingRange.objects.filter(pk=rng.pk, last_used_number__isnull=True).delete()
            return None, 204, {}

        return base.command(request, ctx, f"einvoice.numbering_range.delete:{pk}", fn)


class RangeSync(EinvoiceAPIView):
    def post(self, request: Request) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "einvoice.numbering_range.admin")
        company = base.company_id(request, ctx)
        base.action_body(request)
        started = clock.now().isoformat()
        rows: list[dict[str, Any]] = []
        error: dict[str, Any] | None = None
        if not base.already_used(ctx, request):
            try:
                rows = service.sync_numbering_ranges(ctx, company)
            except (ProviderNotConfigured, AdapterNotSupported) as exc:
                error = exc.to_problem()

        def fn(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            service.apply_synced_ranges(uow, company, rows)
            return (
                _operation(
                    "einvoice_numbering_ranges_sync", error is None, "/api/v1/einvoice/numbering-ranges", started, error
                ),
                202,
                {},
            )

        return base.command(request, ctx, "einvoice.numbering_range.sync", fn)


# ============================================================================ contingency


class ContingencyView(EinvoiceAPIView):
    def get(self, request: Request) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "einvoice.contingency.read")
        return Response(_contingency_json(ctx, base.company_id(request, ctx)))


def _contingency_json(ctx: Any, company: uuid.UUID) -> dict[str, Any]:
    over = service.contingency_overview(ctx, company)
    return {
        "id": over["id"],
        "tenant_id": over["tenant_id"],
        "company_id": over["company_id"],
        "active": over["active"],
        "since": over["since"],
        "version": over["version"],
        "audit": {},
    }


class ContingencyDeclare(EinvoiceAPIView):
    def post(self, request: Request) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "einvoice.contingency.admin")
        company = base.company_id(request, ctx)
        params, reason = base.action_body(request)
        if not reason:
            raise ValidationFailed("reason is required to declare a contingency", code="reason_required")
        kind = str(params.get("kind", "ISSUER_OUTAGE"))

        def fn(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            service.declare_contingency(uow, company, kind, reason)
            return _contingency_json(ctx, company), 200, {}

        return base.command(request, ctx, "einvoice.contingency.declare", fn)


class ContingencyEnd(EinvoiceAPIView):
    def post(self, request: Request) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "einvoice.contingency.admin")
        company = base.company_id(request, ctx)
        base.action_body(request)

        def fn(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            service.end_contingency(uow, company)
            return _contingency_json(ctx, company), 200, {}

        return base.command(request, ctx, "einvoice.contingency.end", fn)


# ============================================================================ RADIAN


def _document_ref(raw: Any) -> DocumentRef:
    if not isinstance(raw, dict) or "type" not in raw or "id" not in raw:
        raise ValidationFailed("document must be {type, id}", field="document")
    return DocumentRef(str(raw["type"])[:80], base.parse_uuid(raw["id"], "document.id"), raw.get("number"))


class RadianList(EinvoiceAPIView):
    def get(self, request: Request) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "einvoice.radian_event.read")
        qs = base.scoped(
            ctx,
            "einvoice.radian_event.read",
            RadianEvent.objects.filter(tenant_id=ctx.tenant_id, company_id=base.company_id(request, ctx)),
        )
        if request.query_params.get("event_code"):
            qs = qs.filter(event_code=request.query_params["event_code"])
        return base.page(request, qs, serializers.radian_event)

    def post(self, request: Request) -> Response:
        """Record an event received from the buyer about one of our invoices."""
        ctx = base.context(request)
        base.authorize(ctx, "einvoice.radian_event.create")
        company = base.company_id(request, ctx)
        data = base.body_object(request, ("document", "event_code"), ("document", "event_code"))
        ref = _document_ref(data["document"])

        def fn(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            ev = service.record_received_event(uow, company, ref, str(data["event_code"]))
            return serializers.radian_event(ev), 201, {"ETag": ev.etag}

        return base.command(request, ctx, "einvoice.radian_event.create", fn)


class RadianDetail(EinvoiceAPIView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "einvoice.radian_event.read")
        qs = base.scoped(
            ctx,
            "einvoice.radian_event.read",
            RadianEvent.objects.filter(tenant_id=ctx.tenant_id, company_id=base.company_id(request, ctx)),
        )
        ev = base.get_or_404(qs, pk)
        return base.with_etag(Response(serializers.radian_event(ev)), ev)


class RadianEmit(EinvoiceAPIView):
    def post(self, request: Request) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "einvoice.radian_event.create")
        company = base.company_id(request, ctx)
        params, _reason = base.action_body(request)
        ref = _document_ref(params.get("document"))
        code = str(params.get("event_code", ""))
        concept = str(params.get("claim_concept", ""))

        def fn(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            ev = service.emit_radian_event(uow, company, ref, code, claim_concept=concept)
            return (
                {
                    "status": ev.status,
                    "resource": {"type": "einvoice.radian_event", "id": str(ev.id), "number": ev.number},
                    "data": {
                        "event_code": ev.event_code,
                        "label": annex.radian_event(code).label,
                        "code_status": annex.radian_event(code).status,
                        "unique_code": ev.unique_code,
                    },
                    "warnings": [],
                },
                200,
                {},
            )

        return base.command(request, ctx, "einvoice.radian_event.emit", fn)


# ============================================================================ habilitación


class TestSetList(EinvoiceAPIView):
    def get(self, request: Request) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "einvoice.habilitation.read")
        qs = base.scoped(
            ctx,
            "einvoice.habilitation.read",
            HabilitationTestSet.objects.filter(tenant_id=ctx.tenant_id, company_id=base.company_id(request, ctx)),
        )
        return base.page(request, qs, serializers.test_set)

    def post(self, request: Request) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "einvoice.habilitation.create")
        company = base.company_id(request, ctx)
        data = base.body_object(request, ("test_set_id",), ("test_set_id",))
        test_set_id = str(data["test_set_id"]).strip()[:255]

        def fn(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            ts = HabilitationTestSet.objects.create(
                tenant_id=ctx.tenant_id,
                company_id=company,
                test_set_id=test_set_id,
                created_by=ctx.principal_id,
                updated_by=ctx.principal_id,
            )
            return serializers.test_set(ts), 201, {"ETag": ts.etag}

        return base.command(request, ctx, "einvoice.habilitation.create", fn)


class TestSetDetail(EinvoiceAPIView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "einvoice.habilitation.read")
        qs = base.scoped(
            ctx,
            "einvoice.habilitation.read",
            HabilitationTestSet.objects.filter(tenant_id=ctx.tenant_id, company_id=base.company_id(request, ctx)),
        )
        ts = base.get_or_404(qs, pk)
        return base.with_etag(Response(serializers.test_set(ts)), ts)


class TestSetRun(EinvoiceAPIView):
    def post(self, request: Request, pk: uuid.UUID) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "einvoice.habilitation.update")
        company = base.company_id(request, ctx)
        params, _ = base.action_body(request)
        qs = base.scoped(
            ctx,
            "einvoice.habilitation.update",
            HabilitationTestSet.objects.filter(tenant_id=ctx.tenant_id, company_id=company),
        )
        ts = base.get_or_404(qs, pk)
        started = clock.now().isoformat()
        results: dict[str, Any] = {}
        if not base.already_used(ctx, request):
            required = params.get("required")
            if required is not None and not isinstance(required, dict):
                raise ValidationFailed("parameters.required must be an object", field="parameters.required")
            results = habilitation.run(ctx, ts, required)

        def fn(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            obj = HabilitationTestSet.objects.select_for_update().get(pk=ts.pk)
            obj.results = results
            obj.runs += 1
            obj.status = (
                HabilitationTestSet.Status.PASSED if results.get("passed") else HabilitationTestSet.Status.FAILED
            )
            obj.save_versioned(ctx.principal_id, ["results", "runs", "status"])
            return (
                _operation(
                    "einvoice_habilitation_test_sets_by_run",
                    bool(results.get("passed")),
                    f"/api/v1/einvoice/habilitation/test-sets/{pk}",
                    started,
                ),
                202,
                {},
            )

        return base.command(request, ctx, f"einvoice.habilitation.run:{pk}", fn)


# ============================================================================ certificates (metadata only)


class CertificateList(EinvoiceAPIView):
    def get(self, request: Request) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "einvoice.certificate.read")
        qs = base.scoped(
            ctx,
            "einvoice.certificate.read",
            SigningCertificate.objects.filter(tenant_id=ctx.tenant_id, company_id=base.company_id(request, ctx)),
        )
        return base.page(request, qs, serializers.certificate)


class CertificateDetail(EinvoiceAPIView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "einvoice.certificate.read")
        qs = base.scoped(
            ctx,
            "einvoice.certificate.read",
            SigningCertificate.objects.filter(tenant_id=ctx.tenant_id, company_id=base.company_id(request, ctx)),
        )
        cert = base.get_or_404(qs, pk)
        return base.with_etag(Response(serializers.certificate(cert)), cert)


# ============================================================================ settings


class SettingsView(EinvoiceAPIView):
    def get(self, request: Request) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "einvoice.settings.read")
        company = base.company_id(request, ctx)
        row = service.read_settings(ctx, company)
        if row is None:
            row = EinvoiceSettings(tenant_id=ctx.tenant_id, company_id=company, id=uuid.UUID(int=0), version=0)
        return Response(serializers.settings(row))

    def put(self, request: Request) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "einvoice.settings.admin")
        company = base.company_id(request, ctx)
        data = base.body_object(
            request, ("mode", "adapter", "provider_id", "software_id", "software_pin_ref"), ("mode", "adapter")
        )
        mode_value, adapter = str(data["mode"]), str(data["adapter"])
        if mode_value not in EinvoiceSettings.Mode.values:
            raise ValidationFailed("mode must be SANDBOX_SIMULATOR or PROVIDER_SANDBOX", field="mode")
        if adapter not in ("simulator", "provider", "direct"):
            raise ValidationFailed("adapter must be simulator, provider or direct", field="adapter")
        if adapter == "direct":
            raise ProviderNotConfigured("the direct DIAN adapter [A] is gated by EG-1/EG-2 (disabled in DEV)")
        if (mode_value == "PROVIDER_SANDBOX") != (adapter == "provider"):
            raise ValidationFailed(
                "PROVIDER_SANDBOX goes with adapter=provider, SANDBOX_SIMULATOR with simulator", field="mode"
            )
        provider_id = base.parse_uuid(data["provider_id"], "provider_id") if data.get("provider_id") else None
        if adapter == "provider" and provider_id is None:
            raise ValidationFailed("provider_id is required with adapter=provider", field="provider_id")
        pin_ref = sec.validate_ref(data["software_pin_ref"]) if data.get("software_pin_ref") else None
        software_id = data.get("software_id")

        def fn(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            row = service.get_settings(uow, company)
            row = EinvoiceSettings.objects.select_for_update(of=("self",)).select_related("provider").get(pk=row.pk)
            headers: dict[str, str] = {}
            fields = ["mode", "adapter"]
            applied = True
            if adapter == "provider":
                provider = TechnologyProvider.objects.filter(
                    tenant_id=ctx.tenant_id,
                    company_id=company,
                    pk=present(provider_id, "provider_id"),
                    status=TechnologyProvider.Status.ACTIVE,
                ).first()
                if provider is None:
                    raise ProviderNotConfigured("provider not found or archived")
                applied = service.select_provider(uow, row, provider)
                fields += ["provider", "pending_provider", "pending_requested_by"]
                if not applied:
                    headers["Warning"] = '299 - "provider switch pending approval by a second administrator"'
            if applied:
                row.mode, row.adapter = mode_value, adapter
                if adapter == "simulator":
                    row.provider = None
                    fields.append("provider")
            if pin_ref is not None:
                row.software_pin_ref = pin_ref
                fields.append("software_pin_ref")
            if software_id is not None:
                row.software_id = str(software_id)[:255]
                fields.append("software_id")
            row.save_versioned(ctx.principal_id, sorted(set(fields)))
            service._audit(
                uow,
                "einvoice.settings.updated",
                "einvoice.settings",
                row.id,
                company,
                {"mode": row.mode, "adapter": row.adapter, "applied": applied},
            )
            return serializers.settings(row), 200, headers

        return base.command(request, ctx, "einvoice.settings.replace", fn)


# ============================================================================ technology providers (F-PT-OPEN)

PROVIDER_FIELDS = ("code", "name", "adapter", "base_url", "environment", "credentials_ref", "contract_version")
SENSITIVE_PROVIDER_FIELDS = frozenset({"adapter", "base_url", "environment", "credentials_ref", "contract_version"})


def _provider_values(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in PROVIDER_FIELDS:
        if key not in data:
            continue
        value = "" if data[key] is None else str(data[key]).strip()
        if key == "code" and not 1 <= len(value) <= 40:
            raise ValidationFailed("code must have 1..40 characters", field="code")
        if key == "name" and not 1 <= len(value) <= 200:
            raise ValidationFailed("name must have 1..200 characters", field="name")
        if key == "adapter" and value not in set(registry.keys()):
            raise ValidationFailed(f"adapter must be one of {', '.join(registry.keys())}", field="adapter")
        if key == "environment" and value != "SANDBOX":
            raise ValidationFailed("only SANDBOX providers exist in the DEV-only edition", field="environment")
        if key == "base_url" and (len(value) > 2000 or not re.match(r"^https?://[^\s/$.?#].[^\s]*$", value)):
            raise ValidationFailed("base_url must be an absolute http(s) URL", field="base_url")
        if key == "credentials_ref":
            value = sec.validate_ref(value) if value else ""
        out[key] = value
    return out


def _providers(ctx: Any, company: uuid.UUID, permission: str) -> Any:
    qs = TechnologyProvider.objects.filter(tenant_id=ctx.tenant_id, company_id=company).exclude(
        status=TechnologyProvider.Status.ARCHIVED
    )
    return base.scoped(ctx, permission, qs)


class ProviderList(EinvoiceAPIView):
    def get(self, request: Request) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "einvoice.provider.read")
        return base.page(
            request, _providers(ctx, base.company_id(request, ctx), "einvoice.provider.read"), serializers.provider
        )

    def post(self, request: Request) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "einvoice.provider.create")
        company = base.company_id(request, ctx)
        data = base.body_object(request, PROVIDER_FIELDS, ("code", "name", "adapter", "base_url", "environment"))
        values = _provider_values(data)

        def fn(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            if TechnologyProvider.objects.filter(
                tenant_id=ctx.tenant_id, company_id=company, code=values["code"]
            ).exists():
                raise Conflict("a provider with this code exists", code="duplicate")
            p = TechnologyProvider.objects.create(
                tenant_id=ctx.tenant_id,
                company_id=company,
                created_by=ctx.principal_id,
                updated_by=ctx.principal_id,
                **values,
            )
            service._audit(
                uow,
                "einvoice.provider.created",
                "einvoice.provider",
                p.id,
                company,
                {"code": p.code, "adapter": p.adapter, "credentials": "***" if p.credentials_ref else None},
            )
            return serializers.provider(p), 201, {"ETag": p.etag}

        return base.command(request, ctx, "einvoice.provider.create", fn, payload=sec.redact_obj(_hash_refs(data)))


def _hash_refs(data: dict[str, Any]) -> dict[str, Any]:
    """Idempotency hash input without the raw secret references (stored only as a digest)."""
    import hashlib

    return {k: (hashlib.sha256(str(v).encode()).hexdigest() if k in SECRET_REF_FIELDS else v) for k, v in data.items()}


class ProviderDetail(EinvoiceAPIView):
    def _get(self, request: Request, permission: str) -> tuple[Any, uuid.UUID, TechnologyProvider]:
        ctx = base.context(request)
        base.authorize(ctx, permission)
        company = base.company_id(request, ctx)
        return ctx, company, base.get_or_404(_providers(ctx, company, permission), self.kwargs["pk"])

    def get(self, request: Request, pk: uuid.UUID) -> Response:
        _, _, p = self._get(request, "einvoice.provider.read")
        return base.with_etag(Response(serializers.provider(p)), p)

    def patch(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company, p = self._get(request, "einvoice.provider.update")
        p.check_if_match(request.headers.get("If-Match"))
        data = base.body_object(request, PROVIDER_FIELDS)
        values = _provider_values(data)
        if not values:
            raise ValidationFailed("empty patch")
        sensitive = SENSITIVE_PROVIDER_FIELDS & set(values)
        if sensitive and p.config_state == TechnologyProvider.ConfigState.ACTIVE:
            raise Conflict(
                "the active provider's connection cannot change: register a new provider and switch",
                code="validation_failed",
            )

        def fn(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            obj = TechnologyProvider.objects.select_for_update().get(pk=p.pk)
            for k, v in values.items():
                setattr(obj, k, v)
            fields = list(values)
            if sensitive:
                obj.config_state = TechnologyProvider.ConfigState.DRAFT
                obj.tested_fingerprint = ""
                fields += ["config_state", "tested_fingerprint"]
            obj.save_versioned(ctx.principal_id, fields)
            service._audit(
                uow, "einvoice.provider.updated", "einvoice.provider", obj.id, company, {"fields": sorted(values)}
            )
            return serializers.provider(obj), 200, {"ETag": obj.etag}

        return base.command(request, ctx, f"einvoice.provider.update:{pk}", fn, payload=_hash_refs(data))

    def delete(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company, p = self._get(request, "einvoice.provider.delete")
        if p.config_state == TechnologyProvider.ConfigState.ACTIVE:
            raise Conflict(
                "the active provider cannot be removed: switch to another provider first", code="validation_failed"
            )

        def fn(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            obj = TechnologyProvider.objects.select_for_update().get(pk=p.pk)
            obj.status, obj.config_state = TechnologyProvider.Status.ARCHIVED, TechnologyProvider.ConfigState.RETIRED
            obj.save_versioned(ctx.principal_id, ["status", "config_state"])
            service._audit(uow, "einvoice.provider.archived", "einvoice.provider", obj.id, company)
            return None, 204, {}

        return base.command(request, ctx, f"einvoice.provider.delete:{pk}", fn)


class ProviderTestConnection(EinvoiceAPIView):
    def post(self, request: Request, pk: uuid.UUID) -> Response:
        ctx = base.context(request)
        base.authorize(ctx, "einvoice.provider.admin")
        company = base.company_id(request, ctx)
        base.action_body(request)
        p = base.get_or_404(_providers(ctx, company, "einvoice.provider.admin"), pk)
        if p.adapter == "http_generic" and (not p.credentials_ref or sec.resolve(p.credentials_ref) is None):
            raise ProviderNotConfigured("the operator has not provided the provider credentials (secret file)")
        report: dict[str, Any] = {}
        if not base.already_used(ctx, request):
            report = service.run_provider_contract(ctx, p)

        def fn(uow: UnitOfWork) -> tuple[Any, int, dict[str, str]]:
            obj = service.record_provider_test(uow, p, report)
            return (
                {
                    "status": "PASSED",
                    "resource": {"type": "einvoice.provider", "id": str(obj.id)},
                    "data": {"config_state": obj.config_state, "report": report, "label": "SIMULADO — no certificado"},
                    "warnings": [],
                },
                200,
                {},
            )

        return base.command(request, ctx, f"einvoice.provider.test:{pk}", fn)


class ProviderExport(EinvoiceAPIView):
    def get(self, request: Request) -> HttpResponse:
        ctx = base.context(request)
        base.authorize(ctx, "einvoice.provider.export")
        company = base.company_id(request, ctx)
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(serializers.PROVIDER_EXPORT_COLUMNS)
        for p in _providers(ctx, company, "einvoice.provider.export").order_by("code"):
            writer.writerow([sec.redact(v) for v in serializers.provider_export_row(p)])
        resp = HttpResponse(buf.getvalue(), content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="proveedores-tecnologicos.csv"'
        return resp


# ============================================================================ KPIs


class KpiView(EinvoiceAPIView):
    def get(self, request: Request) -> Response:
        ctx = base.context(request)
        company = base.company_id(request, ctx)
        if not any(services_allowed(ctx, p) for p in ("einvoice.document.read", "einvoice.numbering_range.read")):
            base.authorize(ctx, "einvoice.document.read")
        body = kpis.facturacion_kpis(ctx, company, request.query_params.get("period"))
        if services_allowed(ctx, "einvoice.contingency.read"):
            over = service.contingency_overview(ctx, company)
            body["panels"]["contingencia"] = {
                k: over[k]
                for k in (
                    "active",
                    "kind",
                    "since",
                    "restored_at",
                    "deadline_at",
                    "remaining_seconds",
                    "overdue",
                    "pending_documents",
                )
            }
        return Response(body)


def services_allowed(ctx: Any, permission: str) -> bool:
    from kernel import services
    from kernel.interfaces.policy import PolicyService

    return bool(services.get(PolicyService).authorize(ctx, permission).allowed)
