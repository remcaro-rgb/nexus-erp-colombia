"""Persisted formulas: DRAFT → (validate) → PENDING_APPROVAL → ACTIVE → RETIRED.

This is the only module of ``workflow.formula`` that touches the database;
the engine modules stay pure. ACTIVE versions are immutable (a change is a
new version); author ≠ approver (SoD); AI agents may only draft.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from kernel import clock
from kernel.errors import Conflict, ImmutableRecord, NotFound, PermissionDenied, ValidationFailed
from kernel.tenancy import PrincipalType, TenantContext
from kernel.uow import UnitOfWork, run_in_unit_of_work
from workflow.audit.service import record_change
from workflow.formula.engine import DOMAINS, EvaluationResult, FormulaDefinition, FormulaGraph, validate_definition
from workflow.formula.errors import FormulaNotApproved, FormulaUnknownIdentifier
from workflow.formula.schema import check_table_name
from workflow.formula.tables import VersionedTable
from workflow.policy import policy

AGENT_TYPES = (PrincipalType.AI_AGENT, PrincipalType.MCP_CLIENT)
EDITABLE = ("description", "expression", "inputs", "result_type", "output_places", "output_rounding", "depends_on",
            "test_cases", "consumers", "effective_from", "effective_to")  # fmt: skip


def definition_of(row: Any) -> FormulaDefinition:
    return FormulaDefinition.from_json(
        {
            "code": row.code,
            "expression": row.expression,
            "inputs": row.inputs,
            "result_type": row.result_type,
            "depends_on": row.depends_on,
            "output_places": row.output_places,
            "output_rounding": row.output_rounding,
            "tests": row.test_cases,
            "version": row.formula_version,
        }
    )


@dataclass(frozen=True)
class DbTableResolver:
    """Lookup tables of exactly one company, effective on one date."""

    tenant_id: uuid.UUID
    company_id: uuid.UUID
    on: dt.date
    accessed: list[tuple[str, int]] = field(default_factory=list, compare=False)

    def get(self, name: str) -> VersionedTable:
        from django.db.models import Q

        from workflow.models import FormulaTable

        check_table_name(name)
        row = (
            FormulaTable.objects.filter(tenant_id=self.tenant_id, company_id=self.company_id, name=name)
            .filter(Q(effective_from__isnull=True) | Q(effective_from__lte=self.on))
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=self.on))
            .order_by("-version")
            .first()
        )
        if row is None:
            raise FormulaUnknownIdentifier(f"table '{name}' is not available for this company and date")
        table = VersionedTable.build(
            row.company_id, row.name, row.rows, key_type=row.key_type, value_type=row.value_type, version=row.version
        )
        self.accessed.append((name, row.version))
        return table


class FormulaService:
    # ------------------------------------------------------------------ helpers
    def _get(self, ctx: TenantContext, formula_id: uuid.UUID, *, lock: bool = False) -> Any:
        from workflow.models import Formula

        qs = Formula.objects.select_for_update() if lock else Formula.objects
        row = qs.filter(tenant_id=ctx.tenant_id, company_id__in=list(ctx.company_ids), id=formula_id).first()
        if row is None:
            raise NotFound("formula not found")
        return row

    def active_definitions(self, tenant_id: uuid.UUID, company_id: uuid.UUID, domain: str, on: dt.date | None = None,
                           exclude_code: str | None = None) -> list[FormulaDefinition]:  # fmt: skip
        from workflow.models import Formula

        on = on or clock.business_date()
        rows = Formula.objects.filter(tenant_id=tenant_id, company_id=company_id, domain=domain, status=Formula.ACTIVE)
        best: dict[str, Any] = {}
        for r in rows:
            if (r.effective_from and r.effective_from > on) or (r.effective_to and r.effective_to < on):
                continue
            if r.code == exclude_code:
                continue
            if r.code not in best or r.formula_version > best[r.code].formula_version:
                best[r.code] = r
        return [definition_of(r) for r in best.values()]

    @staticmethod
    def _clean(data: Mapping[str, Any]) -> dict[str, Any]:
        out = {k: data[k] for k in EDITABLE if k in data}
        if "test_cases" not in out and "tests" in data:
            tests = data["tests"]
            out["test_cases"] = tests.get("cases", []) if isinstance(tests, Mapping) else tests
        for k in ("effective_from", "effective_to"):
            if isinstance(out.get(k), str):
                out[k] = dt.date.fromisoformat(out[k])
        return out

    # ------------------------------------------------------------------ commands
    def create(self, ctx: TenantContext, company_id: uuid.UUID, data: Mapping[str, Any],
               idempotency_key: str | None = None) -> Any:  # fmt: skip
        return run_in_unit_of_work(lambda uow: self.create_in(uow, company_id, data), ctx,
                                   idempotency_key=idempotency_key)  # fmt: skip

    def create_in(self, uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> Any:
        """Create a DRAFT (next version of its code) inside the caller's unit of work."""
        from django.db.models import Max

        from workflow.models import Formula

        ctx = uow.ctx
        ctx.require_company(company_id)
        policy().require(ctx, "workflow.formula.create", {"company_id": company_id})
        domain = data.get("domain", "approval")
        if domain not in DOMAINS:
            raise ValidationFailed(f"domain must be one of {DOMAINS}")
        values = self._clean(data)
        definition = FormulaDefinition.from_json({**data, "tests": values.get("test_cases", [])})  # shape + names
        last = Formula.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, code=definition.code).aggregate(
            v=Max("formula_version")
        )["v"]
        row = Formula.objects.create(
            tenant_id=ctx.tenant_id, company_id=company_id, domain=domain, code=definition.code,
            formula_version=(last or 0) + 1, author_id=ctx.on_behalf_of or ctx.principal_id,
            created_by=ctx.principal_id, updated_by=ctx.principal_id, hash=definition.hash, **values,
        )  # fmt: skip
        record_change("workflow.formula.create", "workflow.formula", row.id, company_id=company_id,
                      after={"code": row.code, "version": row.formula_version, "expression": row.expression,
                             "by_agent": ctx.principal_type in AGENT_TYPES}, uow=uow)  # fmt: skip
        return row

    def update(self, ctx: TenantContext, formula_id: uuid.UUID, patch: Mapping[str, Any], if_match: str | None) -> Any:
        policy().require(ctx, "workflow.formula.update")

        def work(uow: UnitOfWork) -> Any:
            row = self._get(ctx, formula_id, lock=True)
            row.check_if_match(if_match)
            if row.status != "DRAFT":
                raise ImmutableRecord("only DRAFT formulas can be edited; create a new version")
            values = self._clean(patch)
            before = {k: getattr(row, k) for k in values}
            for k, v in values.items():
                setattr(row, k, v)
            row.hash = definition_of(row).hash
            row.save_versioned(ctx.principal_id, [*values, "hash"])
            record_change(
                "workflow.formula.update",
                "workflow.formula",
                row.id,
                company_id=row.company_id,
                before=before,
                after=values,
                uow=uow,
            )
            return row

        return run_in_unit_of_work(work, ctx)

    def validate(self, ctx: TenantContext, company_id: uuid.UUID, data: Mapping[str, Any], *, domain: str = "approval",
                 require_tests: bool = False, on: dt.date | None = None) -> Any:  # fmt: skip
        ctx.require_company(company_id)
        policy().require(ctx, "workflow.formula.read")
        definition = FormulaDefinition.from_json(data)
        others = self.active_definitions(ctx.tenant_id, company_id, domain, on, exclude_code=definition.code)
        resolver = DbTableResolver(ctx.tenant_id, company_id, on or clock.business_date())
        return validate_definition(definition, others, tables=resolver, require_tests=require_tests)

    def simulate(self, ctx: TenantContext, company_id: uuid.UUID, data: Mapping[str, Any], inputs: Mapping[str, Any],
                 *, domain: str = "approval", on: dt.date | None = None) -> EvaluationResult:  # fmt: skip
        ctx.require_company(company_id)
        policy().require(ctx, "workflow.formula.read")
        definition = FormulaDefinition.from_json(data)
        others = self.active_definitions(ctx.tenant_id, company_id, domain, on, exclude_code=definition.code)
        graph = FormulaGraph([*others, definition])
        resolver = DbTableResolver(ctx.tenant_id, company_id, on or clock.business_date())
        return graph.evaluate(inputs, targets=[definition.code], tables=resolver)[definition.code]

    def submit(self, ctx: TenantContext, formula_id: uuid.UUID) -> Any:
        policy().require(ctx, "workflow.formula.update")

        def work(uow: UnitOfWork) -> Any:
            row = self._get(ctx, formula_id, lock=True)
            if row.status != "DRAFT":
                raise Conflict(f"only DRAFT formulas can be submitted (status {row.status})")
            report = self.validate(ctx, row.company_id, self._as_json(row), domain=row.domain, require_tests=True,
                                   on=row.effective_from)  # fmt: skip
            row.last_test_run = {
                "at": clock.now().isoformat(), "ok": report.ok,
                "tests": [{"name": t.name, "passed": t.passed} for t in report.tests],
            }  # fmt: skip
            row.save(update_fields=["last_test_run"])
            report.raise_for_errors()
            row.status, row.submitted_by, row.hash = "PENDING_APPROVAL", ctx.principal_id, report.hash or row.hash
            row.save_versioned(ctx.principal_id, ["status", "submitted_by", "hash"])
            record_change(
                "workflow.formula.submit",
                "workflow.formula",
                row.id,
                company_id=row.company_id,
                before={"status": "DRAFT"},
                after={"status": row.status, "hash": row.hash},
                uow=uow,
            )
            return row

        return run_in_unit_of_work(work, ctx)

    def approve(self, ctx: TenantContext, formula_id: uuid.UUID, reason: str = "") -> Any:
        from workflow.models import Formula

        if ctx.principal_type in AGENT_TYPES:
            raise PermissionDenied("AI agents cannot approve formulas", code="human_approval_required")
        policy().require(ctx, "workflow.formula.approve")

        def work(uow: UnitOfWork) -> Any:
            row = self._get(ctx, formula_id, lock=True)
            if row.status != "PENDING_APPROVAL":
                raise Conflict("only submitted formulas can be approved")
            if ctx.principal_id in (row.author_id, row.submitted_by, row.created_by):
                raise PermissionDenied("the author cannot approve their own formula (SoD)", code="sod_violation")
            if definition_of(row).hash != row.hash:
                raise Conflict("the formula changed after submission", code="formula_hash_mismatch")
            starts = row.effective_from or clock.business_date()
            for prev in Formula.objects.select_for_update().filter(
                tenant_id=row.tenant_id, company_id=row.company_id, code=row.code, status=Formula.ACTIVE
            ):
                if prev.effective_to is None or prev.effective_to >= starts:
                    prev.effective_to = starts - dt.timedelta(days=1)
                    if prev.effective_from and prev.effective_to < prev.effective_from:
                        prev.status, prev.retired_at = Formula.RETIRED, clock.now()
                    prev.save(update_fields=["effective_to", "status", "retired_at"])
            row.status, row.approved_by, row.approved_at = Formula.ACTIVE, ctx.principal_id, clock.now()
            row.effective_from = starts
            row.save_versioned(ctx.principal_id, ["status", "approved_by", "approved_at", "effective_from"])
            uow.emit("workflow.formula.approved.v1",
                     {"formula_id": str(row.id), "code": row.code, "version_no": row.formula_version},
                     company_id=row.company_id)  # fmt: skip
            record_change(
                "workflow.formula.approve",
                "workflow.formula",
                row.id,
                company_id=row.company_id,
                before={"status": "PENDING_APPROVAL"},
                after={"status": row.status},
                reason=reason,
                uow=uow,
            )
            return row

        return run_in_unit_of_work(work, ctx)

    def retire(self, ctx: TenantContext, formula_id: uuid.UUID, reason: str = "") -> Any:
        policy().require(ctx, "workflow.formula.void")

        def work(uow: UnitOfWork) -> Any:
            row = self._get(ctx, formula_id, lock=True)
            if row.status == "RETIRED":
                raise Conflict("already retired")
            before = row.status
            row.status, row.retired_at = "RETIRED", clock.now()
            row.save_versioned(ctx.principal_id, ["status", "retired_at"])
            record_change(
                "workflow.formula.retire",
                "workflow.formula",
                row.id,
                company_id=row.company_id,
                before={"status": before},
                after={"status": "RETIRED"},
                reason=reason,
                uow=uow,
            )
            return row

        return run_in_unit_of_work(work, ctx)

    def versions(self, ctx: TenantContext, formula_id: uuid.UUID) -> list[Any]:
        from workflow.models import Formula

        row = self._get(ctx, formula_id)
        return list(
            Formula.objects.filter(tenant_id=row.tenant_id, company_id=row.company_id, code=row.code).order_by(
                "formula_version"
            )
        )

    # ------------------------------------------------------------------ callers (pricing, payroll, approvals)
    def evaluate_for_document(
        self, uow: UnitOfWork, company_id: uuid.UUID, domain: str, code: str, inputs: Mapping[str, Any], *,
        on: dt.date, document_type: str = "", document_id: uuid.UUID | None = None,
    ) -> tuple[EvaluationResult, Any]:  # fmt: skip
        """Evaluate the ACTIVE version effective ``on`` and store its snapshot in the caller's UoW."""
        from workflow.models import Formula, FormulaEvaluation

        ctx = uow.ctx
        ctx.require_company(company_id)
        defs = self.active_definitions(ctx.tenant_id, company_id, domain, on)
        if code not in {d.code for d in defs}:
            raise FormulaNotApproved(f"no ACTIVE version of formula '{code}' on {on.isoformat()}")
        resolver = DbTableResolver(ctx.tenant_id, company_id, on)
        result = FormulaGraph(defs).evaluate(inputs, targets=[code], tables=resolver)[code]
        row = Formula.objects.get(
            tenant_id=ctx.tenant_id, company_id=company_id, code=code, formula_version=result.version
        )
        snap = FormulaEvaluation.objects.create(
            tenant_id=ctx.tenant_id, company_id=company_id, formula=row, code=code, version=result.version,
            hash=result.formula_hash, document_type=document_type, document_id=document_id,
            snapshot=result.snapshot(row.id),
        )  # fmt: skip
        return result, snap

    # ------------------------------------------------------------------ import / export
    @staticmethod
    def _as_json(row: Any) -> dict[str, Any]:
        return {
            "code": row.code, "domain": row.domain, "version": row.formula_version, "description": row.description,
            "expression": row.expression, "inputs": row.inputs, "result_type": row.result_type,
            "output_places": row.output_places, "output_rounding": row.output_rounding,
            "depends_on": row.depends_on, "tests": row.test_cases, "consumers": row.consumers,
            "effective_from": row.effective_from.isoformat() if row.effective_from else None,
            "effective_to": row.effective_to.isoformat() if row.effective_to else None,
        }  # fmt: skip

    def export(self, ctx: TenantContext, company_id: uuid.UUID) -> list[dict[str, Any]]:
        from workflow.models import Formula

        policy().require(ctx, "workflow.formula.read")
        ctx.require_company(company_id)
        rows = Formula.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id).order_by(
            "code", "formula_version"
        )
        return [{**self._as_json(r), "status": r.status, "hash": r.hash} for r in rows]

    def import_json(self, ctx: TenantContext, company_id: uuid.UUID, items: list[Mapping[str, Any]]) -> list[Any]:
        """Imported formulas are always DRAFT and must pass their tests before submit."""
        return [self.create(ctx, company_id, {k: v for k, v in item.items() if k not in ("status", "hash", "version")})
                for item in items]  # fmt: skip
