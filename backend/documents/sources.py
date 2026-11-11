"""Source-record data providers: (source_type, id) → (template code, plain render data, number).

Each provider reads the source document **through the same policy path as its own API** (``PolicyService``
``require`` + ``scope_queryset`` of the owning resource; foreign or out-of-scope ids answer 404) and applies the
owner's field mask (a principal who cannot read the confidential ``party`` sees it masked on the PDF too).

Registered providers: ``treasury.receipt`` (recibo de caja, F-TRE-RC) and ``treasury.disbursement`` (comprobante de
egreso, F-TRE-CE). Other modules register theirs with :func:`register` (CCR P17a-2).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from kernel import services
from kernel.errors import NotFound, ValidationFailed
from kernel.interfaces.policy import PolicyService
from kernel.tenancy import TenantContext

MASK = "••••••"


@dataclass(frozen=True, slots=True)
class SourceData:
    template_code: str
    data: dict[str, Any]
    number: str
    watermark: str | None = None


Provider = Callable[[TenantContext, uuid.UUID, uuid.UUID], SourceData]
_PROVIDERS: dict[str, Provider] = {}


def register(source_type: str, provider: Provider) -> None:
    _PROVIDERS[source_type] = provider


def provider(source_type: str) -> Provider:
    found = _PROVIDERS.get(source_type)
    if found is None:
        raise ValidationFailed(f"no document source {source_type!r}", pointer="/parameters/source_type")
    return found


def source_types() -> list[str]:
    return sorted(_PROVIDERS)


def load(ctx: TenantContext, company_id: uuid.UUID, source_type: str, source_id: uuid.UUID) -> SourceData:
    return provider(source_type)(ctx, company_id, source_id)


# --------------------------------------------------------------------------- shared lookups


def money(amount: Decimal | None, currency: str) -> dict[str, str] | None:
    if amount is None:
        return None
    return {"amount": format(amount, "f"), "currency": currency}


def company_info(company_id: uuid.UUID) -> dict[str, str]:
    from iam.models import Company

    c = Company.objects.filter(id=company_id).values("legal_name", "nit", "dv").first()
    if c is None:
        return {"name": "", "nit": "", "dv": ""}
    return {"name": c["legal_name"], "nit": c["nit"], "dv": c["dv"] or ""}


def branch_info(company_id: uuid.UUID, branch_id: uuid.UUID | None) -> dict[str, str]:
    if branch_id is None:
        return {}
    from iam.models import Branch

    b = Branch.objects.filter(company_id=company_id, id=branch_id).values("code", "name").first()
    return {"code": b["code"], "name": b["name"]} if b else {}


def principal_name(principal_id: Any) -> str:
    """Display name of a user principal (IAM user), else a short stable id (never an e-mail address)."""
    if not principal_id:
        return ""
    from iam.models import User

    pid = uuid.UUID(str(principal_id))
    user = User.objects.filter(id=pid).values("display_name").first()
    if user and user["display_name"]:
        return str(user["display_name"])
    return f"Usuario {str(pid)[:8]}"


def party_info(company_id: uuid.UUID, party_id: uuid.UUID | None, kind: str) -> dict[str, str]:
    if party_id is None:
        return {}
    from purchasing.models import Supplier
    from sales.models import Party

    p = Party.objects.filter(company_id=company_id, id=party_id).values("legal_name", "nit", "dv").first()
    if p:
        return {"name": p["legal_name"], "tax_id": f"{p['nit']}-{p['dv']}" if p["dv"] else p["nit"]}
    s = (
        Supplier.objects.filter(company_id=company_id).filter(id=party_id).values("name", "nit").first()
        or Supplier.objects.filter(company_id=company_id, party_id=party_id).values("name", "nit").first()
    )
    if s:
        return {"name": s["name"], "tax_id": s["nit"]}
    if kind == "other":
        info = company_info(party_id)
        if info["name"]:
            return {"name": info["name"], "tax_id": info["nit"]}
    return {"name": f"Tercero {str(party_id)[:8]}", "tax_id": ""}


# --------------------------------------------------------------------------- treasury RC / CE

KIND_LABELS = {
    "AR_APPLICATION": "Abono a cartera",
    "AP_APPLICATION": "Pago a proveedor",
    "ADVANCE": "Anticipo",
    "CONCEPT": "Concepto",
    "WITHHOLDING": "Retención",
    "ROUNDING": "Ajuste al peso",
}
METHOD_LABELS = {
    "CASH": "Efectivo",
    "TRANSFER": "Transferencia",
    "CHECK": "Cheque",
    "CARD": "Tarjeta",
    "OTHER": "Otro",
}
STATUS_LABELS = {
    "DRAFT": "Borrador",
    "SUBMITTED": "Pendiente de aprobación",
    "APPROVED": "Aprobado",
    "REJECTED": "Rechazado",
    "POSTED": "Contabilizado",
    "REVERSED": "Reversado",
    "CANCELLED": "Anulado",
}
FUND_LABELS = {"BANK": "Banco", "CASH": "Caja"}
TREASURY_WATERMARK = "SINTÉTICO — sin validez fiscal (DEV)"


def _treasury_voucher(resource: str, voucher_type: str) -> Provider:
    def load_voucher(ctx: TenantContext, company_id: uuid.UUID, source_id: uuid.UUID) -> SourceData:
        from treasury.models import TreasuryVoucher

        policy = services.get(PolicyService)
        policy.require(ctx, f"{resource}.read")
        qs = policy.scope_queryset(
            ctx, f"{resource}.read", TreasuryVoucher.objects.filter(company_id=company_id, voucher_type=voucher_type)
        )
        v = qs.select_related("fund").filter(id=source_id).first()
        if v is None:
            raise NotFound(f"{resource} not found")
        readable = policy.field_mask(ctx, resource).readable
        cur = v.currency
        lines: list[dict[str, Any]] = []
        for ln in v.lines.select_related("concept").order_by("line_no"):
            description = ln.description
            if ln.open_item_document and ln.open_item_document.get("number"):
                description = f"{description} · {ln.open_item_document['number']}".strip(" ·")
            if ln.concept is not None and not description:
                description = ln.concept.name
            if ln.kind == "WITHHOLDING" and ln.tax_code:
                description = f"{description or 'Retención'} ({ln.tax_code})"
            lines.append(
                {
                    "line_no": ln.line_no,
                    "kind": ln.kind,
                    "kind_label": KIND_LABELS.get(ln.kind, ln.kind),
                    "description": description or KIND_LABELS.get(ln.kind, ln.kind),
                    "base": money(ln.tax_base, cur) if ln.kind == "WITHHOLDING" else None,
                    "amount": money(ln.amount, cur),
                }
            )
        if "party" in readable:
            party = party_info(company_id, v.party_id, v.party_kind)
        else:
            party = {"name": MASK, "tax_id": ""} if v.party_id else {}
        is_rc = voucher_type == "RC"
        approvers = [principal_name(a) for a in (v.approved_by or [])]
        data = {
            "title": "RECIBO DE CAJA" if is_rc else "COMPROBANTE DE EGRESO",
            "number": v.number,
            "document_date": v.document_date.isoformat(),
            "posting_date": v.posting_date.isoformat() if v.posting_date else None,
            "status": v.status,
            "status_label": STATUS_LABELS.get(v.status, v.status),
            "party_role": "Recibido de" if is_rc else "Pagado a",
            "party": party,
            "fund": {
                "code": v.fund.code,
                "name": v.fund.name,
                "kind": FUND_LABELS.get(v.fund.fund_type, v.fund.fund_type),
            },
            "payment_method": v.payment_method,
            "payment_method_label": METHOD_LABELS.get(v.payment_method, v.payment_method),
            "reference": v.reference,
            "currency": cur,
            "company": company_info(company_id),
            "branch": branch_info(company_id, v.branch_id),
            "lines": lines,
            "withholding_total": money(v.withholding_total, cur) if v.withholding_total else None,
            "total_label": "TOTAL RECIBIDO" if is_rc else "TOTAL PAGADO",
            "total": money(v.total, cur),
            "notes": v.notes,
            "prepared_by": {"name": principal_name(v.created_by)},
            "approved_by": [{"name": n} for n in approvers],
            "approved_by_names": ", ".join(approvers),
            "journal_entry": str(v.posted_entry_id) if v.posted_entry_id else None,
        }
        return SourceData(resource, data, v.number, TREASURY_WATERMARK)

    return load_voucher


register("treasury.receipt", _treasury_voucher("treasury.receipt", "RC"))
register("treasury.disbursement", _treasury_voucher("treasury.disbursement", "CE"))
