"""Employee portal API (``/api/portal/v1/employee/*``, F-PORTAL-EMP; contract ``portal.yaml``, owner P13).

Security (D-018, D-033, F-PORTAL-CORE): ``erp-portal`` tokens of ACTIVE employee accounts only
(:class:`iam.external.portal.PortalView`); the employee and company come **only** from the token's account.
Every read is scoped in the repository layer — querysets through ``PolicyEngine.scope_queryset`` (company +
``employee_id``), payslips through the payslip source called with the token's employee plus an ownership check
of every returned document; a foreign id is 404 ``portal_document_not_owned`` (no existence oracle). Scope ids
in the query string or body are refused (``external_account_scope_violation``). Responses are field allowlists:
no other employee's data, no aggregates over other employees, no internal costs. Every document access is
audited; the per-principal rate limit of the secured base applies.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from django.http import HttpResponse
from rest_framework.request import Request
from rest_framework.response import Response

from hr import services
from hr.errors import PortalDocumentNotOwned
from hr.interfaces import EmployeePayslipView
from hr.models import DataUpdateRequest, Employee
from hr.portal import certificates, kpis
from hr.portal.payslips import source
from iam.api.base import DEFAULT_LIMIT, MAX_LIMIT, parse_uuid, to_json
from iam.errors import FilterInvalid
from iam.external.portal import PortalView
from iam.policy.engine import get_engine
from kernel import clock, services as registry, tenancy
from kernel.errors import ValidationFailed
from kernel.interfaces.documents import DocumentRenderer
from kernel.money import q_amount
from kernel.uow import UnitOfWork, unit_of_work
from timekeeping import services as tk
from timekeeping.models import VACATION, Absence


def _audit(view: PortalView, action: str, resource: str, obj_id: Any) -> None:
    from kernel.interfaces.audit import AuditEntry, AuditService

    svc = registry.optional(AuditService)
    if svc is None:
        return
    portal = view.effective.portal
    with unit_of_work(view.ctx) as uow:
        svc.record(uow, AuditEntry(action, resource, obj_id, portal.company_id if portal else None, {"channel": "portal"}))


class EmployeePortalView(PortalView):
    portal_kind = "employee"
    permissions: ClassVar[dict[str, str]] = {}

    def employee(self) -> Employee:
        portal = self.effective.portal
        assert portal is not None  # noqa: S101 - PortalView.check_access guarantees it
        return services.employee_of(self.ctx.tenant_id, portal.company_id, portal.employee_id)

    def owned(self, resource: Mapping[str, Any]) -> bool:
        return get_engine().portal_owns(self.effective, resource)

    def page(self, request: Request, items: Sequence[dict[str, Any]]) -> Response:
        """Cursor pagination over an already owner-scoped list (no filters/sorts on portal lists)."""
        for key in request.query_params:
            if key not in ("cursor", "limit", "fields", *self.ignored_params):
                raise FilterInvalid(f"unknown filter {key}")
        try:
            limit = int(request.query_params.get("limit", DEFAULT_LIMIT))
            offset = int(base64.urlsafe_b64decode(request.query_params["cursor"].encode()).decode()) if request.query_params.get("cursor") else 0
        except (ValueError, UnicodeDecodeError) as exc:
            raise FilterInvalid("invalid cursor or limit") from exc
        if not 1 <= limit <= MAX_LIMIT or offset < 0:
            raise FilterInvalid("limit must be between 1 and 500")
        chunk = items[offset : offset + limit]
        nxt = base64.urlsafe_b64encode(str(offset + limit).encode()).decode() if len(items) > offset + limit else None
        return Response({"items": [self.serialize(i) for i in chunk], "next_cursor": nxt})


# --------------------------------------------------------------------------- payslips


def payslip_dict(p: EmployeePayslipView) -> dict[str, Any]:
    return {
        "id": p.id,
        "tenant_id": p.tenant_id,
        "status": p.status,
        "period": p.period,
        "net_pay": {"amount": str(q_amount(p.net_pay)), "currency": "COP"},
        "version": p.version,
        "audit": {"channel": "portal"},
        "employee_id": p.employee_id,
        "company_id": p.company_id,
    }


class _PayslipBase(EmployeePortalView):
    permissions: ClassVar[dict[str, str]] = {"GET": "portal.employee_payslip.read"}
    portal_fields = ("id", "tenant_id", "status", "period", "net_pay", "version", "audit")

    def to_dict(self, obj: Any) -> dict[str, Any]:
        return obj if isinstance(obj, dict) else payslip_dict(obj)

    def own_payslips(self, emp: Employee) -> list[EmployeePayslipView]:
        with tenancy.scope(self.ctx):
            rows = source().payslips_of(self.ctx.tenant_id, emp.company_id, emp.id)
        return [p for p in rows if p.tenant_id == emp.tenant_id and self.owned(payslip_dict(p))]

    def own_payslip(self, emp: Employee, pk: str) -> EmployeePayslipView:
        with tenancy.scope(self.ctx):
            slip = source().payslip(self.ctx.tenant_id, emp.company_id, parse_uuid(pk))
        if slip is None or slip.tenant_id != emp.tenant_id or not self.owned(payslip_dict(slip)):
            raise PortalDocumentNotOwned("not found")
        return slip


class PayslipsView(_PayslipBase):
    def get(self, request: Request, pk: str | None = None) -> Response:
        with tenancy.scope(self.ctx):
            emp = self.employee()
        if pk is None:
            return self.page(request, [payslip_dict(p) for p in self.own_payslips(emp)])
        slip = self.own_payslip(emp, pk)
        _audit(self, "portal.employee.payslip_viewed", "payroll.payslip", slip.id)
        return Response(self.serialize(payslip_dict(slip)))


def _pdf(content: bytes, filename: str) -> HttpResponse:
    response = HttpResponse(content, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Cache-Control"] = "no-store"
    return response


class PayslipPdfView(_PayslipBase):
    def get(self, request: Request, pk: str) -> HttpResponse:
        with tenancy.scope(self.ctx):
            emp = self.employee()
        slip = self.own_payslip(emp, pk)
        labels = " · ".join(certificates._label(s) for s in slip.parameters if isinstance(s, dict) and s.get("code"))
        data = {
            "employee": emp.full_name,
            "period": slip.period,
            "earnings": str(q_amount(slip.earnings)),
            "deductions": str(q_amount(slip.deductions)),
            "net_pay": str(q_amount(slip.net_pay)),
            "lines": [dict(ln) for ln in slip.lines],
            "legal_notice": slip.legal_notice or certificates.WATERMARK,
            "parameter_status": labels,
        }
        doc = registry.get(DocumentRenderer).render(
            "hr.desprendible", data, watermark=f"{certificates.WATERMARK} · Parámetros: {labels or 'ninguno'}"
        )
        _audit(self, "portal.employee.payslip_downloaded", "payroll.payslip", slip.id)
        return _pdf(doc.content, f"desprendible-{slip.period}.pdf")


# --------------------------------------------------------------------------- certificates


def certificate_dict(c: certificates.Certificate, emp: Employee) -> dict[str, Any]:
    return {
        "id": c.id,
        "tenant_id": c.tenant_id,
        "status": "AVAILABLE",
        "certificate_type": c.certificate_type,
        "year": c.year,
        "version": 1,
        "audit": {"channel": "portal"},
        "employee_id": emp.id,
        "company_id": emp.company_id,
    }


class _CertificateBase(_PayslipBase):
    permissions: ClassVar[dict[str, str]] = {"GET": "portal.employee_certificate.read"}
    portal_fields = ("id", "tenant_id", "status", "certificate_type", "year", "version", "audit")

    def certificates(self) -> tuple[Employee, list[certificates.Certificate], list[EmployeePayslipView]]:
        with tenancy.scope(self.ctx):
            emp = self.employee()
            slips = self.own_payslips(emp)
            return emp, certificates.available(emp, slips, clock.business_date()), slips

    def find(self, pk: str) -> tuple[Employee, certificates.Certificate, list[EmployeePayslipView]]:
        wanted = parse_uuid(pk)
        emp, certs, slips = self.certificates()
        for c in certs:
            if c.id == wanted:
                return emp, c, slips
        raise PortalDocumentNotOwned("not found")


class CertificatesView(_CertificateBase):
    def get(self, request: Request, pk: str | None = None) -> Response:
        if pk is None:
            emp, certs, _ = self.certificates()
            return self.page(request, [certificate_dict(c, emp) for c in certs])
        emp, cert, _ = self.find(pk)
        return Response(self.serialize(certificate_dict(cert, emp)))


class CertificatePdfView(_CertificateBase):
    def get(self, request: Request, pk: str) -> HttpResponse:
        emp, cert, slips = self.find(pk)
        from iam.models import Company

        with tenancy.scope(self.ctx):
            company = Company.objects.filter(pk=emp.company_id).values_list("legal_name", flat=True).first() or ""
            template, data = certificates.render_data(cert, emp, company, slips, clock.business_date())
        doc = registry.get(DocumentRenderer).render(template, data, watermark=certificates.watermark(data))
        _audit(self, "portal.employee.certificate_downloaded", "hr.certificate", cert.id)
        return _pdf(doc.content, f"certificado-{cert.certificate_type.lower()}-{cert.year}.pdf")


# --------------------------------------------------------------------------- vacations


def vacation_dict(a: Absence) -> dict[str, Any]:
    from hr.vacations import request_status

    return {
        "id": a.id,
        "tenant_id": a.tenant_id,
        "status": a.status,
        "start_date": a.start_date,
        "end_date": a.end_date,
        "days": format(a.days.normalize(), "f"),
        "request_status": request_status(a, clock.business_date()),
        "version": a.version,
        "audit": {"created_at": a.created_at.isoformat(), "channel": "portal" if a.channel == "portal" else "api"},
    }


class _Collection(EmployeePortalView):
    model: ClassVar[Any] = None
    read_permission: ClassVar[str] = ""

    def scoped(self) -> Any:
        return get_engine().scope_queryset(self.ctx, self.read_permission, self.base_qs())

    def base_qs(self) -> Any:
        return self.model.objects.all()

    def list_or_get(self, request: Request, pk: str | None) -> Response:
        qs = self.scoped()
        if pk is None:
            return self.paginate(request, qs)
        with tenancy.scope(self.ctx):
            obj = qs.filter(pk=parse_uuid(pk)).first()
            if obj is None:
                raise PortalDocumentNotOwned("not found")
            return Response(self.serialize(obj))

    def body(self, request: Request, allowed: tuple[str, ...]) -> Mapping[str, Any]:
        data = request.data if isinstance(request.data, Mapping) else {}
        unknown = sorted(set(data) - set(allowed))
        if unknown:
            raise ValidationFailed("unknown fields", fields=unknown)
        return data


class VacationsView(_Collection):
    permissions: ClassVar[dict[str, str]] = {"GET": "portal.employee_vacation.read", "POST": "portal.employee_vacation.create"}
    read_permission = "portal.employee_vacation.read"
    model = Absence
    portal_fields = ("id", "tenant_id", "status", "start_date", "end_date", "days", "request_status", "version", "audit")

    def base_qs(self) -> Any:
        return Absence.objects.filter(absence_type=VACATION)

    def to_dict(self, obj: Any) -> dict[str, Any]:
        return vacation_dict(obj)

    def get(self, request: Request, pk: str | None = None) -> Response:
        return self.list_or_get(request, pk)

    def post(self, request: Request, pk: str | None = None) -> Response:
        if pk is not None:
            raise ValidationFailed("POST is only allowed on the collection")
        data = self.body(request, ("start_date", "end_date"))

        def run(uow: UnitOfWork) -> tuple[Any, int]:
            emp = Employee.objects.select_for_update().get(pk=self.employee().pk)  # serialise balance checks
            absence = tk.new_absence(uow, emp, {**data, "absence_type": VACATION}, channel="portal")
            tk.submit_absence(uow, absence)
            return self.serialize(absence), 201

        response = self.mutate(request, run, scope="portal.employee_vacation.create")
        if response.status_code == 201 and isinstance(response.data, dict) and "id" in response.data:
            response["Location"] = f"/api/portal/v1/employee/vacations/{response.data['id']}"
        return response


# --------------------------------------------------------------------------- data updates


def data_update_portal_dict(r: DataUpdateRequest) -> dict[str, Any]:
    return {
        "id": r.id,
        "tenant_id": r.tenant_id,
        "status": r.review_status,
        "changes": r.changes,
        "review_status": r.review_status,
        "version": r.version,
        "audit": {"created_at": r.created_at.isoformat(), "channel": "portal"},
    }


class DataUpdatesView(_Collection):
    permissions: ClassVar[dict[str, str]] = {
        "GET": "portal.employee_data_update.read",
        "POST": "portal.employee_data_update.create",
    }
    read_permission = "portal.employee_data_update.read"
    model = DataUpdateRequest
    portal_fields = ("id", "tenant_id", "status", "changes", "review_status", "version", "audit")

    def to_dict(self, obj: Any) -> dict[str, Any]:
        return data_update_portal_dict(obj)

    def get(self, request: Request, pk: str | None = None) -> Response:
        return self.list_or_get(request, pk)

    def post(self, request: Request, pk: str | None = None) -> Response:
        if pk is not None:
            raise ValidationFailed("POST is only allowed on the collection")
        data = self.body(request, ("changes",))

        def run(uow: UnitOfWork) -> tuple[Any, int]:
            req = services.submit_data_update(uow, self.employee(), data.get("changes"))
            return self.serialize(req), 201

        response = self.mutate(request, run, scope="portal.employee_data_update.create")
        if response.status_code == 201 and isinstance(response.data, dict) and "id" in response.data:
            response["Location"] = f"/api/portal/v1/employee/data-updates/{response.data['id']}"
        return response


# --------------------------------------------------------------------------- profile + KPIs


class ProfileView(EmployeePortalView):
    permissions: ClassVar[dict[str, str]] = {"GET": "portal.employee_profile.read"}
    portal_fields = ("id", "tenant_id", "full_name", "email", "position", "version", "audit")

    def to_dict(self, obj: Any) -> dict[str, Any]:
        return {
            "id": obj.id,
            "tenant_id": obj.tenant_id,
            "full_name": obj.full_name,
            "email": obj.email,
            "position": obj.position.name if obj.position_id and obj.position else None,
            "version": obj.version,
            "audit": {"updated_at": obj.updated_at.isoformat(), "channel": "portal"},
        }

    def get(self, request: Request) -> Response:
        with tenancy.scope(self.ctx):
            emp = self.employee()
            return Response(self.serialize(emp))


class KpisView(EmployeePortalView):
    """``GET /api/portal/v1/employee/kpis`` — the portal dashboard KPIs (CCR P13a-2: route not yet in the contract)."""

    permissions: ClassVar[dict[str, str]] = {"GET": "portal.employee_profile.read"}
    portal_fields = ("as_of", "kpis", "generated_at", "portal")

    def get(self, request: Request) -> Response:
        with tenancy.scope(self.ctx):
            emp = self.employee()
            body = kpis.employee_kpis(self, emp)
        return Response({k: to_json(v) for k, v in body.items()})
