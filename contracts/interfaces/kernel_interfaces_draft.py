"""DRAFT of the kernel service interfaces (P02 contract freeze, for P01's `backend/kernel/interfaces/` — frozen v1 at G1).

P01 owns `backend/kernel/interfaces/*.py`; this file is the contract-side draft that P01 copies/splits (CCR-P02-2). Every Protocol documents
pre/postconditions, errors (stable codes from contracts/errors.yaml) and idempotency. Money, quantities, rates are `Decimal` (D-001); ids are UUIDv7.
Implementations raise `ContractError(code, detail)` with a registered code; nothing here performs I/O.
Interfaces named by FEATURES-ADDENDUM stubs: Dimensions, Notifications, Inventory, Labels, PayrollPayables, FiscalDocuments, PaymentStatus,
DocumentRenderer, ExportFormat (T-X-QA-07 checks P01's real module; test doubles live next to each implementation).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Literal, Mapping, Protocol, Sequence, runtime_checkable
from uuid import UUID

Classification = Literal["public", "internal", "confidential", "restricted-pii", "sensitive-pii", "secret"]
Channel = Literal["ui", "api", "ai", "mcp", "import", "scheduler", "portal", "system"]
ParameterStatus = Literal["VERIFIED-PRIMARY", "VERIFIED-SEARCH", "VERIFIED-SECONDARY", "UNKNOWN", "DISPUTED"]


class ContractError(Exception):
    """Error with a stable code from contracts/errors.yaml (e.g. `stock_insufficient`, `period_closed`)."""

    def __init__(self, code: str, detail: str = "", **data: Any) -> None:
        super().__init__(f"{code}: {detail}")
        self.code, self.detail, self.data = code, detail, data


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str  # ISO 4217


@dataclass(frozen=True)
class Quantity:
    value: Decimal
    uom: str


@dataclass(frozen=True)
class Principal:
    tenant_id: UUID
    user_id: UUID | None
    company_ids: tuple[UUID, ...]
    channel: Channel
    on_behalf_of: UUID | None = None  # delegated (AI/MCP) — permissions are the intersection
    portal_party_id: UUID | None = None  # portal identities only; scope from the token (D-033)


@dataclass(frozen=True)
class DocumentRef:
    type: str  # "<module>.<document>"
    id: UUID
    number: str | None = None


@dataclass(frozen=True)
class DimensionSlots:
    cost_center_id: UUID | None = None
    business_unit_id: UUID | None = None
    aux_abierto_id: UUID | None = None
    item_dimension_id: UUID | None = None
    branch_id: UUID | None = None


@dataclass(frozen=True)
class JournalLine:
    account_code: str
    debit: Money
    credit: Money
    party_id: UUID | None = None
    dimensions: DimensionSlots = field(default_factory=DimensionSlots)


@dataclass(frozen=True)
class PostingRequest:
    company_id: UUID
    source: DocumentRef
    posting_date: dt.date
    journal: str
    lines: tuple[JournalLine, ...]
    rule_set: str  # shared/07 rule set id


@dataclass(frozen=True)
class PostingResult:
    entry_id: UUID
    number: str
    simulated: bool


@dataclass(frozen=True)
class ParameterValue:
    value: Any
    status: ParameterStatus
    source_id: UUID | None
    synthetic: bool  # True for labelled fixtures (NOT LEGALLY VERIFIED)


@dataclass(frozen=True)
class Availability:
    item_id: UUID
    base: Quantity
    reserved: Quantity
    packaged: Mapping[UUID, Quantity]  # presentation_id → packaged qty (Σ packaged × factor ≤ base, D-013)
    loose: Quantity


@dataclass(frozen=True)
class RenderedFile:
    content: bytes
    media_type: str
    filename: str
    synthetic_label: str | None  # e.g. "SINTÉTICO — no válido ante la DIAN"


@runtime_checkable
class PostingService(Protocol):
    """Ledger posting (P05, ADR-005). Called INSIDE the caller's kernel.uow unit of work; never commits by itself.
    Pre: lines balance per currency and in COP after engine rounding; accounts postable; period open; dimension rules satisfied.
    Errors: entry_unbalanced, account_not_postable, period_closed, period_locked, fx_rate_missing, dimension_required, dimension_forbidden,
    dimension_node_not_postable, dimension_cross_company, dimension_archived. Idempotent per (source, rule_set): a second call returns the same entry.
    `simulate` returns the identical entry without persisting and raises the identical errors."""

    def post(self, request: PostingRequest, principal: Principal) -> PostingResult: ...

    def simulate(self, request: PostingRequest, principal: Principal) -> PostingResult: ...

    def reverse(self, entry_id: UUID, reason: str, reversal_date: dt.date, principal: Principal) -> PostingResult: ...


@runtime_checkable
class StockService(Protocol):
    """Stock moves and valuation (P09). Runs inside the caller's UoW; locks quants in (item, warehouse, lot) order.
    Errors: stock_insufficient, reservation_conflict, lot_required, location_not_leaf, uom_precision_violation. Idempotent by (source, line_no)."""

    def issue(self, company_id: UUID, source: DocumentRef, lines: Sequence[Mapping[str, Any]], principal: Principal) -> Sequence[UUID]: ...

    def receive(self, company_id: UUID, source: DocumentRef, lines: Sequence[Mapping[str, Any]], principal: Principal) -> Sequence[UUID]: ...


@runtime_checkable
class Inventory(Protocol):
    """FEATURES-ADDENDUM stub (P09 for P10/P11/P12/P16): presentation- and bin-aware availability, reservations, transfers and receipts.
    `availability` never double counts packaged stock (D-013). `reserve` fails with reservation_conflict/stock_insufficient under row lock.
    `create_transfer` is same-company only (transfer_cross_company, D-012) and returns a DRAFT transfer with reservations.
    `receive` accepts presentation and putaway/bin targets and optional quality hold; shipment notices from the supplier portal create DRAFT receipts."""

    def availability(self, company_id: UUID, item_id: UUID, *, warehouse_id: UUID | None = None, location_id: UUID | None = None,
                     lot_id: UUID | None = None, presentation_id: UUID | None = None) -> Availability: ...

    def reserve(self, company_id: UUID, source: DocumentRef, item_id: UUID, qty: Quantity, *, location_id: UUID | None, lot_id: UUID | None,
                principal: Principal) -> UUID: ...

    def create_transfer(self, company_id: UUID, from_warehouse_id: UUID, to_warehouse_id: UUID, lines: Sequence[Mapping[str, Any]],
                        source: DocumentRef, principal: Principal) -> DocumentRef: ...

    def receive(self, company_id: UUID, source: DocumentRef, lines: Sequence[Mapping[str, Any]], *, quality_hold: bool,
                principal: Principal) -> Sequence[UUID]: ...

    def shipment_notice(self, company_id: UUID, purchase_order: DocumentRef, lines: Sequence[Mapping[str, Any]], principal: Principal) -> DocumentRef: ...


@runtime_checkable
class Dimensions(Protocol):
    """FEATURES-ADDENDUM stub (P05 for P04 role scopes): cost-center and other dimension lookups. Read-only; company-scoped; effective-dated."""

    def subtree(self, company_id: UUID, dimension: str, node_id: UUID, valid_on: dt.date) -> frozenset[UUID]: ...

    def is_postable(self, company_id: UUID, dimension: str, node_id: UUID, on_date: dt.date) -> bool: ...


@runtime_checkable
class TaxService(Protocol):
    """Tax computation (P06). Pure; reads parameters through ParameterProvider. Errors: tax_parameter_unknown (fail closed), tax_code_inactive."""

    def compute(self, company_id: UUID, document_date: dt.date, lines: Sequence[Mapping[str, Any]], party_id: UUID | None) -> Mapping[str, Any]: ...


@runtime_checkable
class ParameterProvider(Protocol):
    """kernel.parameters (P06 implements). Raises ContractError('parameter_unknown') for UNKNOWN/missing values and
    ContractError('parameter_not_verified') in production without legal-review stamp; DISPUTED returns with a warning snapshot (C1-9)."""

    def get(self, code: str, at_date: dt.date, company_id: UUID | None) -> ParameterValue: ...


@runtime_checkable
class PolicyService(Protocol):
    """iam.policy (P04, ADR-004): the single authorization path for API, UI, export, report, AI, MCP, portals. Deny by default."""

    def authorize(self, principal: Principal, permission: str, resource: Any | None = None, context: Mapping[str, Any] | None = None) -> bool: ...

    def scope_queryset(self, principal: Principal, queryset: Any, permission: str) -> Any: ...

    def field_mask(self, principal: Principal, resource_type: str) -> Mapping[str, Literal["read", "write", "hidden"]]: ...


@runtime_checkable
class AuditService(Protocol):
    """Hash-chained audit log (P07). Append-only; written in the same UoW as the change."""

    def record(self, principal: Principal, action: str, resource: DocumentRef, diff: Mapping[str, Any]) -> UUID: ...

    def verify_chain(self, tenant_id: UUID) -> bool: ...


@runtime_checkable
class WorkflowService(Protocol):
    """Approvals and SoD (P07). Errors: approver_is_requester, sod_violation, approval_required."""

    def request_approval(self, resource: DocumentRef, policy_key: str, principal: Principal) -> UUID: ...

    def is_approved(self, resource: DocumentRef) -> bool: ...


@runtime_checkable
class SchedulerService(Protocol):
    """Scheduled jobs (P07), cron evaluated in the owner company's timezone."""

    def schedule(self, job_type: str, cron: str, payload: Mapping[str, Any], principal: Principal) -> UUID: ...


@runtime_checkable
class NotificationService(Protocol):
    """Notifications (P07). Outbox-driven; DEV mail goes to the local capture only (Mailpit). Never sends inside a posting transaction."""

    def notify(self, recipients: Iterable[UUID], template: str, data: Mapping[str, Any], principal: Principal) -> UUID: ...


@runtime_checkable
class Notifications(Protocol):
    """FEATURES-ADDENDUM stub name for NotificationService (portal invitations, approvals, payment-intent results)."""

    def send(self, channel: Literal["in_app", "email"], to: str, template: str, data: Mapping[str, Any]) -> UUID: ...


@runtime_checkable
class FiscalGateway(Protocol):
    """einvoice.ports.FiscalGateway (P15, ADR-007). DEV: SandboxSimulator only; production endpoints refused (production_endpoint_refused)."""

    def submit(self, document: DocumentRef, ubl_xml: bytes) -> str: ...

    def status(self, document: DocumentRef) -> str: ...


@runtime_checkable
class FiscalDocuments(Protocol):
    """FEATURES-ADDENDUM stub (P15 for P10/P11/P12/P13): create the fiscal document for a posted source (outbox, never in the posting
    transaction's I/O) and read its fiscal status (support document / received e-invoice status included). At-least-once, idempotent by source."""

    def request_issue(self, source: DocumentRef, principal: Principal) -> UUID: ...

    def fiscal_status(self, source: DocumentRef) -> Literal["PENDING", "SENT", "ACCEPTED", "REJECTED", "CONTINGENCY", "NOT_APPLICABLE"]: ...


@runtime_checkable
class PaymentStatus(Protocol):
    """FEATURES-ADDENDUM stub (P16 for portals/P10/P11): status of RC (recibo de caja) and CE (comprobante de egreso) per open item."""

    def receipt_status(self, company_id: UUID, open_item: DocumentRef) -> Literal["OPEN", "PARTIAL", "PAID", "REVERSED"]: ...

    def disbursement_status(self, company_id: UUID, open_item: DocumentRef) -> Literal["OPEN", "SCHEDULED", "PARTIAL", "PAID", "REVERSED"]: ...


@runtime_checkable
class PayrollPayables(Protocol):
    """FEATURES-ADDENDUM stub (P13 for P16): payroll liabilities to pay (net pay, social security, withholdings) per run."""

    def payables(self, company_id: UUID, run_id: UUID) -> Sequence[Mapping[str, Any]]: ...


@runtime_checkable
class Labels(Protocol):
    """FEATURES-ADDENDUM stub (P09 for P11): request labels (barcodes) for received lines; returns a label print job id. Rendering via DocumentRenderer."""

    def request_labels(self, company_id: UUID, source: DocumentRef, lines: Sequence[Mapping[str, Any]], template_code: str | None) -> UUID: ...


@runtime_checkable
class DocumentRenderer(Protocol):
    """D-005: all printable documents (labels, RC, CE, portal downloads) are rendered by this contract (P17, WeasyPrint). Pure; deterministic for
    the same template version + data; synthetic data is visibly labelled. Errors: template_render_failed."""

    def render(self, template_code: str, data: Mapping[str, Any], *, media_type: Literal["application/pdf", "image/png"], locale: str = "es-CO") -> RenderedFile: ...


@runtime_checkable
class ReportQueryService(Protocol):
    """Semantic layer (P17, ADR-009): every query passes iam.policy.scope_queryset and field_mask. No raw SQL."""

    def query(self, principal: Principal, query: Mapping[str, Any]) -> Mapping[str, Any]: ...


@runtime_checkable
class ExportFormat(Protocol):
    """FEATURES-ADDENDUM stub (P18): export writer registry (CSV/XLSX/PDF/JSONL and fixed-width/XML for exógena). Rows arrive already filtered
    by row policy and field masks; CSV formula injection is neutralised by the writer."""

    def media_type(self) -> str: ...

    def write(self, columns: Sequence[str], rows: Iterable[Sequence[Any]]) -> bytes: ...


@runtime_checkable
class FileStore(Protocol):
    """Object storage (MinIO in DEV). Content-addressed; malware/type checks before `put` returns (attachment_rejected)."""

    def put(self, tenant_id: UUID, content: bytes, media_type: str, filename: str) -> str: ...

    def get(self, tenant_id: UUID, key: str) -> bytes: ...
