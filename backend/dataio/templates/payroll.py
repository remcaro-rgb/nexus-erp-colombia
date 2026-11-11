"""Payroll template (owner P16a): payroll concepts (DAG of dependencies validated by the owner service).

``payroll.concepts.service`` checks ``payroll.concept.create|update`` itself; the engine checks them too. The
optional formula is resolved by code (domain ``payroll``) inside the job's company only.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.db.models import QuerySet

from dataio.errors import RowError
from dataio.templates.base import Column, Record, Template
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork
from payroll.concepts import service as concepts
from payroll.models import PayrollConcept

TYPES = ("DEVENGO", "DEDUCCION", "APORTE", "PROVISION")


class PayrollConceptsTemplate(Template):
    entity = "payroll-concepts"
    label = "Conceptos de nómina"
    module = "payroll"
    owner = "P16a"
    feature = "F-PAY-CONCEPTS"
    natural_key = ("code",)
    read_permission = "payroll.concept.read"
    create_permission = "payroll.concept.create"
    update_permission = "payroll.concept.update"
    columns = (
        Column("code", required=True, max_length=40, help="Código del concepto", example="BONO_SINT"),
        Column("name", required=True, max_length=200, help="Nombre", example="Bonificación (SINTÉTICO)"),
        Column(
            "concept_type",
            required=True,
            allowed=TYPES,
            help="DEVENGO, DEDUCCION, APORTE o PROVISION",
            example="DEVENGO",
        ),
        Column("formula_code", max_length=40, help="Código de la fórmula de nómina aprobada (vacío = concepto manual)"),
        Column("depends_on", type="list", help="Conceptos de los que depende, separados por | (sin ciclos, D-025)"),
        Column("account_role", max_length=255, help="Rol contable", example="PAYROLL_EXPENSE"),
        Column("salary_base", type="boolean", help="Constitutivo de salario", example="false"),
        Column(
            "electronic_payroll_code",
            max_length=255,
            help="Elemento de nómina electrónica (dato externo, no inventado)",
        ),
    )
    examples = (
        {
            "code": "BONO_SINT",
            "name": "Bonificación (SINTÉTICO)",
            "concept_type": "DEVENGO",
            "account_role": "PAYROLL_EXPENSE",
            "salary_base": "false",
        },
    )

    def find(self, ctx: TenantContext, company_id: uuid.UUID, rec: Record) -> Any:
        return PayrollConcept.objects.filter(
            tenant_id=ctx.tenant_id, company_id=company_id, code__iexact=rec.values["code"]
        ).first()

    def current(self, obj: PayrollConcept) -> dict[str, Any]:
        from workflow.models import Formula

        fcode = (
            Formula.objects.filter(company_id=obj.company_id, id=obj.formula_id).values_list("code", flat=True).first()
            if obj.formula_id
            else None
        )
        return {
            "code": obj.code,
            "name": obj.name,
            "concept_type": obj.concept_type,
            "formula_code": fcode,
            "depends_on": tuple(obj.depends_on or ()),
            "account_role": obj.account_role or None,
            "salary_base": obj.salary_base,
            "electronic_payroll_code": obj.electronic_payroll_code,
        }

    def _data(self, ctx: TenantContext, company_id: uuid.UUID, rec: Record, names: set[str]) -> dict[str, Any]:
        from workflow.models import Formula

        out: dict[str, Any] = {}
        for n in names:
            v = rec.values.get(n)
            if n == "formula_code":
                fid = None
                if v:
                    fid = (
                        Formula.objects.filter(
                            tenant_id=ctx.tenant_id, company_id=company_id, domain="payroll", code__iexact=v
                        )
                        .order_by("-formula_version")
                        .values_list("id", flat=True)
                        .first()
                    )
                    if fid is None:
                        raise RowError(
                            "not_found", f"fórmula de nómina {v!r} no existe en esta empresa", "formula_code"
                        )
                out["formula_id"] = str(fid) if fid else None
            elif n == "depends_on":
                out[n] = list(v or ())
            elif n == "salary_base":
                out[n] = bool(v)
            elif n == "account_role":
                out[n] = v or ""
            else:
                out[n] = v
        return out

    def create(self, uow: UnitOfWork, company_id: uuid.UUID, rec: Record) -> Any:
        return concepts.create(uow, company_id, self._data(uow.ctx, company_id, rec, set(rec.present)))

    def update(self, uow: UnitOfWork, obj: PayrollConcept, rec: Record, changed: set[str]) -> Any:
        return concepts.update(uow, obj.company_id, obj.id, self._data(uow.ctx, obj.company_id, rec, changed), obj.etag)

    def queryset(self, ctx: TenantContext, company_id: uuid.UUID) -> QuerySet[Any]:
        return PayrollConcept.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id).order_by(
            "sort_order", "code"
        )


TEMPLATES = (PayrollConceptsTemplate,)
