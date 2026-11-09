"""Parameter proposal / two-person approval / impact preview (T-P06-09, T-P06-10).

* :func:`propose` — a new value (with a source; official values also need the
  uploaded document reference) enters as PROPOSED and is never used.
* :func:`propose_change` — a correction of an existing row (same code/scope).
* :func:`approve` — by a principal different from the proposer
  (``sod_violation`` otherwise); the row becomes APPROVED, overlapping approved
  rows are closed (``valid_to``) or SUPERSEDED, and ``tax.parameter.approved.v1``
  is emitted in the same unit of work. ``legal_review=True`` also sets the EG-7
  legal-review stamp (the approver asserts that review).
* :func:`impact_preview` — documents of other modules that would change, from
  providers registered with :func:`register_impact_provider`.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from kernel import clock
from kernel.errors import NotFound, ValidationFailed
from kernel.parameters import ParameterStatus
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork
from tax.errors import ParameterSourceRequired, SodViolation, invalid_transition
from tax.models import Parameter, ParameterApproval, SourceRecord
from tax.parameters import values


@dataclass(frozen=True, slots=True)
class ProposalInput:
    code: str
    value_type: str
    value: Any
    valid_from: dt.date
    parameter_status: str
    valid_to: dt.date | None = None
    source_id: uuid.UUID | None = None
    source: dict[str, Any] | None = None  # inline {url, title, accessed_at, method, excerpt_hash}
    company_id: uuid.UUID | None = None
    scope_ref: str = ""
    document_ref: str = ""
    synthetic: bool = False
    notes: str = ""
    legal_history: tuple[dict[str, Any], ...] = ()


def _source(uow: UnitOfWork, data: ProposalInput) -> SourceRecord:
    if data.source_id is not None:
        src = SourceRecord.objects.filter(id=data.source_id).filter(tenant_id__in=[uow.ctx.tenant_id]).first()
        if src is None:
            raise ParameterSourceRequired("source not found for this tenant", field="source_id")
        return src
    if data.source:
        missing = [k for k in ("url", "title", "accessed_at") if not data.source.get(k)]
        if missing:
            raise ParameterSourceRequired(f"source needs {', '.join(missing)}", field="source")
        accessed = data.source["accessed_at"]
        if isinstance(accessed, str):
            accessed = dt.datetime.fromisoformat(accessed)
        if not isinstance(accessed, dt.datetime) or accessed.tzinfo is None:
            raise ValidationFailed("source.accessed_at must be a timezone-aware datetime", field="source.accessed_at")
        return SourceRecord.objects.create(
            tenant_id=uow.ctx.tenant_id,
            register_id=str(data.source.get("register_id", ""))[:20],
            url=str(data.source["url"]),
            title=str(data.source["title"])[:255],
            accessed_at=accessed,
            method=str(data.source.get("method", ""))[:255],
            excerpt_hash=str(data.source.get("excerpt_hash", ""))[:255],
            created_by=uow.ctx.principal_id,
            updated_by=uow.ctx.principal_id,
        )
    raise ParameterSourceRequired("every parameter value needs a source", field="source")


def _validate(data: ProposalInput) -> tuple[str, dict[str, Any]]:
    if not data.code or len(data.code) > 40:
        raise ValidationFailed("code must have 1..40 characters", field="code")
    try:
        status = ParameterStatus(data.parameter_status)
    except ValueError as exc:
        raise ValidationFailed("unknown parameter_status", field="parameter_status") from exc
    if data.valid_to is not None and data.valid_to < data.valid_from:
        raise ValidationFailed("valid_to before valid_from", field="valid_to")
    if status is ParameterStatus.UNKNOWN:
        return "unknown", {}
    if not data.synthetic and not data.document_ref:  # UNKNOWN returned above
        raise ParameterSourceRequired(
            "an official value needs the uploaded source document (document_ref)", field="document_ref"
        )
    return data.value_type, values.normalize(data.value_type, data.value)


def propose(
    uow: UnitOfWork, data: ProposalInput, *, supersedes: Parameter | None = None, reason: str = ""
) -> Parameter:
    if data.company_id is not None:
        uow.ctx.require_company(data.company_id)
    value_type, stored = _validate(data)
    source = _source(uow, data)
    scope = (
        Parameter.Scope.COMPANY
        if data.company_id
        else (Parameter.Scope.MUNICIPALITY if data.scope_ref else Parameter.Scope.GLOBAL)
    )
    param = Parameter.objects.create(
        tenant_id=uow.ctx.tenant_id,
        company_id=data.company_id,
        code=data.code,
        scope=scope,
        scope_ref=data.scope_ref,
        value_type=value_type,
        value=stored,
        valid_from=data.valid_from,
        valid_to=data.valid_to,
        parameter_status=data.parameter_status,
        source=source,
        legal_history=list(data.legal_history),
        synthetic=data.synthetic,
        approval=Parameter.Approval.PROPOSED,
        proposed_by=uow.ctx.principal_id,
        supersedes=supersedes,
        document_ref=data.document_ref,
        notes=data.notes,
        created_by=uow.ctx.principal_id,
        updated_by=uow.ctx.principal_id,
    )
    ParameterApproval.objects.create(
        tenant_id=uow.ctx.tenant_id,
        parameter=param,
        action="PROPOSE",
        principal_id=uow.ctx.principal_id,
        reason=reason[:2000],
    )
    return param


def get_for_update(ctx: TenantContext, parameter_id: uuid.UUID) -> Parameter:
    param = Parameter.objects.select_for_update().filter(id=parameter_id, tenant_id=ctx.tenant_id).first()
    if param is None or (param.company_id is not None and not ctx.can_access_company(param.company_id)):
        raise NotFound("parameter not found")
    return param


def propose_change(uow: UnitOfWork, parameter_id: uuid.UUID, data: ProposalInput, reason: str = "") -> Parameter:
    current = get_for_update(uow.ctx, parameter_id)
    if current.approval != Parameter.Approval.APPROVED:
        raise invalid_transition("only an approved parameter can be changed by a new proposal")
    if data.code != current.code or data.company_id != current.company_id or data.scope_ref != current.scope_ref:
        raise ValidationFailed("a change keeps the code, company and scope of the parameter", field="code")
    return propose(uow, data, supersedes=current, reason=reason)


def _close_overlaps(uow: UnitOfWork, param: Parameter) -> list[Parameter]:
    """Approved rows of the same key overlapping ``param`` are closed before it or superseded."""
    changed: list[Parameter] = []
    others = (
        Parameter.objects.select_for_update()
        .filter(
            tenant_id=param.tenant_id,
            code=param.code,
            company_id=param.company_id,
            scope_ref=param.scope_ref,
            approval=Parameter.Approval.APPROVED,
        )
        .exclude(id=param.id)
    )
    new_end = param.valid_to or dt.date.max
    for other in others:
        other_end = other.valid_to or dt.date.max
        if other_end < param.valid_from or other.valid_from > new_end:
            continue  # no overlap
        event = {"event": "", "date": clock.now().date().isoformat(), "by_parameter": str(param.id)}
        if other.valid_from < param.valid_from:
            if other_end > new_end:
                raise ValidationFailed(
                    "the new value would split an approved range; propose the tail as its own value",
                    field="valid_to",
                )
            other.valid_to = param.valid_from - dt.timedelta(days=1)
            event["event"] = "closed"
            other.legal_history = [*other.legal_history, event]
            other.save_versioned(uow.ctx.principal_id, ["valid_to", "legal_history"])
        else:
            if other_end > new_end:
                raise ValidationFailed("the new value would split an approved range", field="valid_to")
            other.approval = Parameter.Approval.SUPERSEDED
            other.status = "ARCHIVED"
            event["event"] = "superseded"
            other.legal_history = [*other.legal_history, event]
            other.save_versioned(uow.ctx.principal_id, ["approval", "status", "legal_history"])
        changed.append(other)
    return changed


def approve(uow: UnitOfWork, parameter_id: uuid.UUID, *, reason: str = "", legal_review: bool = False) -> Parameter:
    param = get_for_update(uow.ctx, parameter_id)
    if param.approval != Parameter.Approval.PROPOSED:
        raise invalid_transition(f"parameter is {param.approval}, not PROPOSED")
    if param.proposed_by == uow.ctx.principal_id:
        raise SodViolation("two-person rule: the proposer cannot approve the same value")
    if not param.source_id:
        raise ParameterSourceRequired("parameter has no source")
    _close_overlaps(uow, param)
    param.approval = Parameter.Approval.APPROVED
    fields = ["approval"]
    if legal_review:
        param.legal_reviewed_by = uow.ctx.principal_id
        param.legal_reviewed_at = clock.now()
        fields += ["legal_reviewed_by", "legal_reviewed_at"]
    param.save_versioned(uow.ctx.principal_id, fields)
    ParameterApproval.objects.create(
        tenant_id=uow.ctx.tenant_id,
        parameter=param,
        action="APPROVE",
        principal_id=uow.ctx.principal_id,
        reason=reason[:2000],
    )
    uow.emit(
        "tax.parameter.approved.v1",
        {
            "parameter_id": str(param.id),
            "code": param.code,
            "valid_from": param.valid_from.isoformat(),
            "status": param.parameter_status,
        },
        company_id=param.company_id,
    )
    return param


# ----------------------------------------------------------------------------- impact preview

ImpactProvider = Callable[[TenantContext, str, dt.date, dt.date | None, uuid.UUID | None], list[dict[str, Any]]]
_impact_providers: dict[str, ImpactProvider] = {}


def register_impact_provider(name: str, provider: ImpactProvider) -> None:
    """Modules (payroll runs, open invoices…) register a callable listing affected open documents."""
    _impact_providers[name] = provider


def unregister_impact_provider(name: str) -> None:
    _impact_providers.pop(name, None)


def impact_preview(ctx: TenantContext, param: Parameter) -> dict[str, Any]:
    affected: list[dict[str, Any]] = []
    for name in sorted(_impact_providers):
        for doc in _impact_providers[name](ctx, param.code, param.valid_from, param.valid_to, param.company_id):
            affected.append({"provider": name, **doc})
    return {
        "parameter_id": str(param.id),
        "code": param.code,
        "valid_from": param.valid_from.isoformat(),
        "valid_to": param.valid_to.isoformat() if param.valid_to else None,
        "providers": sorted(_impact_providers),
        "affected": affected,
        "count": len(affected),
    }
