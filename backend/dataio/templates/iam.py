"""IAM templates (owner P04): branches (F-ACC-BRANCH) and external-account invitations (F-PORTAL-CORE).

The IAM services leave policy to their views; the engine checks the same codes (``iam.branch.create|update``,
``iam.external_account.create``) per row. References are resolved by natural code inside the job's company only.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.db.models import QuerySet
from django.db.models.functions import Lower

from dataio.errors import RowError
from dataio.templates.base import Column, Record, Template
from iam.branches import services as branches
from iam.external import services as external
from iam.models import Branch, ExternalAccount
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork
from ledger.models import DimensionNode


class BranchesTemplate(Template):
    entity = "branches"
    label = "Sucursales"
    module = "iam"
    owner = "P04"
    feature = "F-ACC-BRANCH"
    natural_key = ("code",)
    read_permission = "iam.branch.read"
    create_permission = "iam.branch.create"
    update_permission = "iam.branch.update"
    columns = (
        Column("code", required=True, max_length=40, help="Código de la sucursal (único por empresa)", example="BOG"),
        Column("name", required=True, max_length=200, help="Nombre", example="Bogotá principal"),
        Column(
            "is_head_office",
            type="boolean",
            help="true para la sede principal (una activa por empresa)",
            example="true",
            synonyms=("sede_principal",),
        ),
        Column("address", max_length=255, help="Dirección", example="Calle 1 # 2-3"),
        Column("municipality_code", max_length=5, help="Código DANE del municipio (5 dígitos)", example="11001"),
        Column("phone", max_length=25, help="Teléfono", example="+57 601 0000000"),
        Column(
            "default_business_unit_code",
            max_length=40,
            help="Código de la unidad de negocio por defecto (misma empresa)",
        ),
        Column("valid_from", type="date", help="Vigente desde", example="2026-01-01"),
        Column("valid_to", type="date", help="Vigente hasta"),
        Column("status", allowed=("ACTIVE", "ARCHIVED"), help="ACTIVE o ARCHIVED", example="ACTIVE"),
    )
    examples = (
        {
            "code": "BOG",
            "name": "Bogotá principal",
            "is_head_office": "true",
            "municipality_code": "11001",
            "valid_from": "2026-01-01",
            "status": "ACTIVE",
        },
    )

    def find(self, ctx: TenantContext, company_id: uuid.UUID, rec: Record) -> Any:
        return (
            Branch.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id)
            .annotate(lc=Lower("code"))
            .filter(lc=str(rec.values["code"]).lower())
            .first()
        )

    def current(self, obj: Branch) -> dict[str, Any]:
        bu = None
        if obj.default_business_unit_id:
            bu = (
                DimensionNode.objects.filter(company_id=obj.company_id, id=obj.default_business_unit_id)
                .values_list("code", flat=True)
                .first()
            )
        return {
            "code": obj.code,
            "name": obj.name,
            "is_head_office": obj.is_head_office,
            "address": obj.address or None,
            "municipality_code": obj.municipality_code,
            "phone": obj.phone,
            "default_business_unit_code": bu,
            "valid_from": obj.valid_from,
            "valid_to": obj.valid_to,
            "status": obj.status,
        }

    def _data(self, ctx: TenantContext, company_id: uuid.UUID, rec: Record, names: set[str]) -> dict[str, Any]:
        v = rec.values
        out: dict[str, Any] = {}
        for n in ("code", "name", "is_head_office", "address", "municipality_code", "phone", "valid_from", "valid_to"):
            if n in names:
                val = v.get(n)
                if n in ("valid_from", "valid_to") and val is not None:
                    val = val.isoformat()
                if n == "address" and val is None:
                    val = ""
                if n == "is_head_office" and val is None:
                    val = False
                if n == "valid_from" and val is None:
                    continue
                out[n] = val
        if "default_business_unit_code" in names:
            code = v.get("default_business_unit_code")
            bu = None
            if code:
                bu = (
                    DimensionNode.objects.filter(
                        tenant_id=ctx.tenant_id, company_id=company_id, dimension="business_unit"
                    )
                    .annotate(lc=Lower("code"))
                    .filter(lc=str(code).lower())
                    .values_list("id", flat=True)
                    .first()
                )
                if bu is None:
                    raise RowError(
                        "not_found",
                        f"unidad de negocio {code!r} no existe en esta empresa",
                        "default_business_unit_code",
                    )
            out["default_business_unit_id"] = str(bu) if bu else None
        return out

    def create(self, uow: UnitOfWork, company_id: uuid.UUID, rec: Record) -> Any:
        branch = branches.create_branch(uow, company_id, self._data(uow.ctx, company_id, rec, set(rec.present)))
        if rec.values.get("status") == "ARCHIVED":
            branch = branches.archive_branch(uow, branch)
        return branch

    def update(self, uow: UnitOfWork, obj: Branch, rec: Record, changed: set[str]) -> Any:
        data = self._data(uow.ctx, obj.company_id, rec, changed - {"status"})
        if data:
            obj = branches.update_branch(uow, obj, data)
        if "status" in changed:
            obj = (
                branches.archive_branch(uow, obj)
                if rec.values.get("status") == "ARCHIVED"
                else branches.unarchive_branch(uow, obj)
            )
        return obj

    def queryset(self, ctx: TenantContext, company_id: uuid.UUID) -> QuerySet[Any]:
        return Branch.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id).order_by("code")


class ExternalInvitationsTemplate(Template):
    entity = "external-account-invitations"
    label = "Invitaciones a cuentas externas (portales)"
    module = "iam"
    owner = "P04"
    feature = "F-PORTAL-CORE"
    natural_key = ("email",)
    read_permission = "iam.external_account.read"
    create_permission = "iam.external_account.create"
    update_permission = "iam.external_account.update"
    updatable = False
    columns = (
        Column(
            "email",
            required=True,
            max_length=254,
            help="Correo de la persona invitada (sintético en dev)",
            example="proveedor@example.invalid",
        ),
        Column(
            "portal_kind",
            required=True,
            allowed=("supplier", "customer", "employee"),
            help="supplier, customer o employee",
            example="supplier",
        ),
        Column(
            "party_code",
            max_length=40,
            help="Código del tercero (portal proveedor/cliente) en esta empresa",
            example="PRV-001",
        ),
        Column("employee_code", max_length=40, help="Código del empleado (portal empleado) en esta empresa"),
    )
    examples = ({"email": "proveedor@example.invalid", "portal_kind": "supplier", "party_code": "PRV-001"},)
    notes = "Solo crea invitaciones (estado INVITED); suspender, reactivar o revocar se hace en Administración."

    def find(self, ctx: TenantContext, company_id: uuid.UUID, rec: Record) -> Any:
        return (
            ExternalAccount.objects.filter(
                tenant_id=ctx.tenant_id, company_id=company_id, email=str(rec.values["email"]).lower()
            )
            .exclude(external_status="REVOKED")
            .first()
        )

    def current(self, obj: ExternalAccount) -> dict[str, Any]:
        from hr.models import Employee
        from sales.models import Party

        party = (
            Party.objects.filter(company_id=obj.company_id, id=obj.party_id).values_list("code", flat=True).first()
            if obj.party_id
            else None
        )
        emp = (
            Employee.objects.filter(company_id=obj.company_id, id=obj.employee_id)
            .values_list("code", flat=True)
            .first()
            if obj.employee_id
            else None
        )
        return {"email": obj.email, "portal_kind": obj.portal_kind, "party_code": party, "employee_code": emp}

    def create(self, uow: UnitOfWork, company_id: uuid.UUID, rec: Record) -> Any:
        from hr.models import Employee
        from sales.models import Party

        ctx = uow.ctx
        v = rec.values
        kind = v["portal_kind"]
        if kind == "employee":
            code = v.get("employee_code")
            if not code:
                raise RowError("required", "employee_code es obligatorio para el portal de empleados", "employee_code")
            emp = Employee.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, code__iexact=code).first()
            if emp is None:
                raise RowError("not_found", f"empleado {code!r} no existe en esta empresa", "employee_code")
            party = {"id": str(emp.id), "kind": "employee"}
        else:
            code = v.get("party_code")
            if not code:
                raise RowError("required", "party_code es obligatorio", "party_code")
            p = Party.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, code__iexact=code).first()
            if p is None:
                raise RowError("not_found", f"tercero {code!r} no existe en esta empresa", "party_code")
            party = {"id": str(p.id), "kind": external.PARTY_KIND[kind]}
        return external.invite(uow, company_id, {"email": v["email"], "portal_kind": kind, "party": party})

    def queryset(self, ctx: TenantContext, company_id: uuid.UUID) -> QuerySet[Any]:
        return (
            ExternalAccount.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id)
            .exclude(external_status="REVOKED")
            .order_by("email")
        )


TEMPLATES = (BranchesTemplate, ExternalInvitationsTemplate)
