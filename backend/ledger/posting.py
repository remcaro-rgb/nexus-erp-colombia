"""PostingService implementation (P05, ADR-005, shared/07).

``post`` runs inside the caller's unit of work: it resolves accounts, converts to
COP, generates rounding lines, checks invariant 1 (balanced per currency and in
COP), period status (invariant 8), control accounts, dimension masters and the
dimension rule matrix (DIM-R01/R04/R08), then writes the entries (one per mirror
book), the lines, the in-line balances and the outbox event. Any error aborts
the caller's whole transaction. ``simulate`` runs exactly the same planning code
and writes nothing.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.db import IntegrityError, connections, transaction
from django.db.models import CharField, F, Func, Value

from kernel import ids, sequences, services
from kernel.errors import Conflict, NotFound, SequenceNotFound, UnitOfWorkRequired, ValidationFailed
from kernel.interfaces.audit import AuditEntry, AuditService
from kernel.interfaces.common import DimensionValues, DocumentRef
from kernel.interfaces.policy import PolicyService
from kernel.interfaces.posting import PostingLine, PostingRequest, PostingResult
from kernel.models import Sequence
from kernel.money import q_amount, q_fx, validate_currency
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork, current_uow
from ledger import rules as rule_matrix
from ledger.dimensions import service as dims
from ledger.errors import ledger_error
from ledger.models import (
    DIMENSION_BY_SLOT,
    SLOT_BY_DIMENSION,
    Account,
    AccountDetermination,
    Book,
    DimensionNode,
    ExchangeRate,
    Journal,
    JournalEntry,
    JournalLine,
    LedgerBalance,
    Period,
)

FUNCTIONAL_CURRENCY = "COP"
ROUNDING_TOLERANCE = Decimal("1.0000")  # shared/07 §8 default (COP 1.00 per document)
ROUNDING_ROLE = "ROUNDING"
FX_ROUNDING_ROLE = "FX_ROUNDING"
SOFT_CLOSED_PERMISSION = "ledger.period.update"  # closing team; dedicated code requested in CCR P05-4
POSTED_EVENT = "ledger.entry.posted.v1"
REVERSED_EVENT = "ledger.entry.reversed.v1"
ZERO = Decimal(0)


def dims_hash(values: dict[str, uuid.UUID | None]) -> str:
    raw = "|".join(str(values.get(slot) or "") for slot in SLOT_BY_DIMENSION.values())
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass
class PlannedLine:
    account: Account
    debit: Decimal
    credit: Decimal
    amount_currency: Decimal
    currency: str
    fx_rate: Decimal
    slots: dict[str, uuid.UUID | None]
    party_id: uuid.UUID | None = None
    party_kind: str = ""
    tax_code: str = ""
    item_id: uuid.UUID | None = None
    warehouse_id: uuid.UUID | None = None
    memo: str = ""
    is_rounding: bool = False

    @property
    def dims_hash(self) -> str:
        return dims_hash(self.slots)

    def to_json(self) -> dict[str, Any]:
        return {
            "account": self.account.code,
            "debit": {"amount": f"{self.debit:.4f}", "currency": FUNCTIONAL_CURRENCY},
            "credit": {"amount": f"{self.credit:.4f}", "currency": FUNCTIONAL_CURRENCY},
            "amount_currency": {"amount": f"{self.amount_currency:.4f}", "currency": self.currency},
            "dimensions": {slot: (str(v) if v else None) for slot, v in self.slots.items()},
            "is_rounding": self.is_rounding,
        }


@dataclass
class Plan:
    request: PostingRequest
    journal: Journal
    period: Period
    books: list[Book]
    lines: list[PlannedLine] = field(default_factory=list)

    @property
    def total_debit(self) -> Decimal:
        return sum((ln.debit for ln in self.lines), ZERO)

    @property
    def total_credit(self) -> Decimal:
        return sum((ln.credit for ln in self.lines), ZERO)

    def to_json(self) -> dict[str, Any]:
        return {
            "journal": self.journal.code,
            "posting_date": self.request.posting_date.isoformat(),
            "period": self.period.code,
            "books": [b.code for b in self.books],
            "total_debit": {"amount": f"{self.total_debit:.4f}", "currency": FUNCTIONAL_CURRENCY},
            "total_credit": {"amount": f"{self.total_credit:.4f}", "currency": FUNCTIONAL_CURRENCY},
            "lines": [ln.to_json() for ln in self.lines],
        }


def _require_active_uow(uow: UnitOfWork) -> None:
    active = current_uow()
    if active is None or active is not uow or not connections[uow.using].in_atomic_block:
        raise UnitOfWorkRequired("PostingService must be called inside the caller's unit of work")


def _slots_of(values: DimensionValues) -> dict[str, uuid.UUID | None]:
    return {slot: getattr(values, slot) for slot in SLOT_BY_DIMENSION.values()}


class LedgerPostingService:
    """Implements :class:`kernel.interfaces.posting.PostingService`."""

    # ------------------------------------------------------------------ public API

    def post(self, uow: UnitOfWork, request: PostingRequest) -> PostingResult:
        _require_active_uow(uow)
        prior = self._existing(uow.ctx, request)
        if prior is not None:
            return PostingResult(prior.id, prior.number, prior.total_debit, prior.total_credit, True)
        plan = self.plan(uow, request)
        return self._persist(uow, plan)

    def simulate(self, uow: UnitOfWork, request: PostingRequest) -> PostingResult:
        _require_active_uow(uow)
        plan = self.plan(uow, request)
        return PostingResult(uuid.UUID(int=0), "SIMULATED", plan.total_debit, plan.total_credit)

    def reverse(self, uow: UnitOfWork, entry_id: uuid.UUID, reversal_date: dt.date, reason: str) -> PostingResult:
        _require_active_uow(uow)
        if not str(reason or "").strip():
            raise ValidationFailed("a reversal needs a reason", pointer="/reason", reason="reason_required")
        ctx = uow.ctx
        original = (
            JournalEntry.objects.select_for_update()
            .filter(tenant_id=ctx.tenant_id, company_id__in=list(ctx.company_ids), id=entry_id)
            .first()
        )
        if original is None:
            raise NotFound("journal entry not found")
        if original.status != JournalEntry.Status.POSTED:
            raise Conflict("the entry is already reversed", code="entry_already_reversed")
        siblings = list(
            JournalEntry.objects.select_for_update()
            .filter(
                tenant_id=ctx.tenant_id,
                company_id=original.company_id,
                source_type=original.source_type,
                source_id=original.source_id,
                purpose=original.purpose,
                status=JournalEntry.Status.POSTED,
            )
            .order_by("book__code")
        )
        period = self._period(ctx, original.company_id, reversal_date, original.journal.code)
        primary_result: PostingResult | None = None
        for entry in siblings:
            lines = []
            for ln in JournalLine.objects.filter(entry=entry).select_related("account").order_by("line_no"):
                lines.append(
                    PlannedLine(
                        account=ln.account,
                        debit=ln.credit,
                        credit=ln.debit,
                        amount_currency=ln.amount_currency,
                        currency=ln.currency,
                        fx_rate=ln.fx_rate,
                        slots={slot: getattr(ln, slot) for slot in SLOT_BY_DIMENSION.values()},
                        party_id=ln.party_id,
                        party_kind=ln.party_kind,
                        tax_code=ln.tax_code,
                        item_id=ln.item_id,
                        warehouse_id=ln.warehouse_id,
                        memo=ln.memo,
                        is_rounding=ln.is_rounding,
                    )
                )
            request = PostingRequest(
                company_id=entry.company_id,
                posting_date=reversal_date,
                source=DocumentRef("ledger.reversal", original.id, original.number),
                purpose=f"reversal:{entry.purpose}"[:120],
                lines=(),
                journal_code=entry.journal.code,
                branch_id=entry.branch_id,
            )
            plan = Plan(request=request, journal=entry.journal, period=period, books=[entry.book], lines=lines)
            new_entry = self._write_entry(uow, plan, entry.book, reversal_of=entry, memo=str(reason)[:500])
            JournalEntry.objects.filter(id=entry.id).update(
                status=JournalEntry.Status.REVERSED, reversed_by_id=new_entry.id
            )
            uow.emit(
                REVERSED_EVENT,
                {"entry_id": str(entry.id), "reversal_entry_id": str(new_entry.id), "reason": str(reason)[:10000]},
                company_id=entry.company_id,
            )
            if entry.id == original.id:
                primary_result = PostingResult(
                    new_entry.id, new_entry.number, new_entry.total_debit, new_entry.total_credit
                )
        if primary_result is None:  # pragma: no cover - original is always among siblings
            raise NotFound("journal entry not found")
        return primary_result

    # ------------------------------------------------------------------ planning (shared by post/simulate)

    def plan(self, uow: UnitOfWork, request: PostingRequest) -> Plan:
        ctx = uow.ctx
        company_id = ctx.require_company(request.company_id)
        if not request.lines:
            raise ValidationFailed("an entry needs lines", pointer="/lines", reason="required")
        if not request.source.type or not request.purpose:
            raise ValidationFailed("source.type and purpose are required", pointer="/source", reason="required")
        journal = Journal.objects.filter(
            tenant_id=ctx.tenant_id, company_id=company_id, code=request.journal_code
        ).first()
        if journal is None:
            raise ValidationFailed(
                f"journal {request.journal_code} not configured", pointer="/journal_code", reason="not_found"
            )
        period = self._period(ctx, company_id, request.posting_date, journal.code)
        books = list(
            Book.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, mirror=True).order_by(
                "-primary", "code"
            )
        )
        if not books:
            raise ValidationFailed("no accounting book configured", pointer="/books", reason="not_found")
        plan = Plan(request=request, journal=journal, period=period, books=books)
        accounts: dict[str, Account] = {}
        for i, line in enumerate(request.lines):
            plan.lines.append(self._plan_line(ctx, request, journal, line, i, accounts))
        self._balance(ctx, plan)
        self._check_dimensions(ctx, plan)
        return plan

    def _period(self, ctx: TenantContext, company_id: uuid.UUID, day: dt.date, journal_code: str) -> Period:
        period = (
            Period.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, start__lte=day, end__gte=day)
            .order_by("number")
            .first()
        )
        if period is None:
            raise ledger_error("period_closed", f"no accounting period is defined for {day}")
        # FOR SHARE: concurrent postings do not block each other, a close (UPDATE) waits for them.
        status = (
            Period.objects.filter(pk=period.pk)
            .annotate(
                locked_status=Func(
                    Value(period.pk), function="ledger_period_status_for_share", output_field=CharField()
                )
            )
            .values_list("locked_status", flat=True)
            .first()
        )
        period.status = status or Period.Status.CLOSED
        if period.status == Period.Status.CLOSED:
            raise ledger_error("period_closed", f"period {period.code} is closed")
        if period.status == Period.Status.SOFT_CLOSED:
            policy = services.optional(PolicyService)
            if (
                policy is None
                or not policy.authorize(ctx, SOFT_CLOSED_PERMISSION, {"period_id": str(period.id)}).allowed
            ):
                raise ledger_error("period_closed", f"period {period.code} is soft-closed")
        return period

    def _account_for(
        self,
        ctx: TenantContext,
        request: PostingRequest,
        journal: Journal,
        line: PostingLine,
        cache: dict[str, Account],
    ) -> Account:
        key = f"{line.account_role}|{line.tax_code or ''}|{line.warehouse_id or ''}"
        if key in cache:
            return cache[key]
        company_id = request.company_id
        if line.account_role.startswith("account:"):
            code = line.account_role.split(":", 1)[1]
            account = Account.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, code=code).first()
            if account is None:
                raise ledger_error("account_determination_missing", f"account {code} does not exist", account=code)
        else:
            candidates = AccountDetermination.objects.select_related("account").filter(
                tenant_id=ctx.tenant_id, company_id=company_id, role_code=line.account_role, active=True
            )
            best: tuple[int, int, AccountDetermination] | None = None
            for det in candidates:
                if det.document_type and det.document_type != request.source.type:
                    continue
                if det.journal_code and det.journal_code != journal.code:
                    continue
                if det.tax_code and det.tax_code != (line.tax_code or ""):
                    continue
                if det.warehouse_id and det.warehouse_id != line.warehouse_id:
                    continue
                specificity = sum(
                    bool(x) for x in (det.document_type, det.journal_code, det.tax_code, det.warehouse_id)
                )
                rank = (det.priority, specificity, det)
                if best is None or rank[:2] > best[:2]:
                    best = rank
            if best is None:
                raise ledger_error(
                    "account_determination_missing",
                    f"no account determination for role {line.account_role}",
                    role=line.account_role,
                )
            account = best[2].account
        cache[key] = account
        return account

    def _rate(self, ctx: TenantContext, company_id: uuid.UUID, line: PostingLine, day: dt.date) -> Decimal:
        currency = validate_currency(line.currency)
        if currency == FUNCTIONAL_CURRENCY:
            if line.fx_rate != 1:
                raise ValidationFailed("COP lines use rate 1", pointer="/lines/fx_rate", reason="fx_rate_cop")
            return Decimal(1)
        if line.fx_rate != 1:
            if line.fx_rate <= 0:
                raise ValidationFailed("fx_rate must be positive", pointer="/lines/fx_rate", reason="range")
            return q_fx(line.fx_rate)
        rate = (
            ExchangeRate.objects.filter(
                tenant_id=ctx.tenant_id, company_id=company_id, currency=currency, date__lte=day
            )
            .order_by("-date")
            .values_list("rate", flat=True)
            .first()
        )
        if rate is None:
            raise ledger_error("fx_rate_missing", f"no exchange rate for {currency} on {day}", currency=currency)
        return Decimal(rate)

    def _plan_line(
        self,
        ctx: TenantContext,
        request: PostingRequest,
        journal: Journal,
        line: PostingLine,
        index: int,
        cache: dict[str, Account],
    ) -> PlannedLine:
        if not isinstance(line.amount, Decimal):
            raise ValidationFailed("amounts must be Decimal", pointer=f"/lines/{index}/amount", reason="type")
        if line.amount < 0:
            raise ValidationFailed("amounts must not be negative", pointer=f"/lines/{index}/amount", reason="range")
        if line.side not in ("DEBIT", "CREDIT"):
            raise ValidationFailed("side must be DEBIT or CREDIT", pointer=f"/lines/{index}/side", reason="enum")
        account = self._account_for(ctx, request, journal, line, cache)
        if not account.active or not account.postable:
            raise ledger_error("account_not_postable", f"account {account.code} is not postable", account=account.code)
        if account.is_control and not any(request.source.type.startswith(p) for p in (account.control_sources or [])):
            raise ledger_error(
                "account_not_postable",
                f"account {account.code} is a control account; only its sub-ledgers post to it",
                account=account.code,
                reason="control_account",
            )
        if account.currency_restriction and account.currency_restriction != line.currency:
            raise ledger_error(
                "account_not_postable", f"account {account.code} only accepts {account.currency_restriction}"
            )
        rate = self._rate(ctx, request.company_id, line, request.posting_date)
        amount_currency = q_amount(line.amount)
        cop = q_amount(amount_currency * rate)
        return PlannedLine(
            account=account,
            debit=cop if line.side == "DEBIT" else ZERO,
            credit=cop if line.side == "CREDIT" else ZERO,
            amount_currency=amount_currency,
            currency=line.currency,
            fx_rate=rate,
            slots=_slots_of(line.dimensions),
            party_id=line.party.id if line.party else None,
            party_kind=line.party.kind if line.party else "",
            tax_code=line.tax_code or "",
            item_id=line.item_id,
            warehouse_id=line.warehouse_id,
            memo=line.memo[:500],
        )

    def _balance(self, ctx: TenantContext, plan: Plan) -> None:
        """Invariant 1: Σdebit = Σcredit per transaction currency and in COP.

        A residual within the tolerance becomes an engine-generated ROUNDING line
        (single currency) or FX_ROUNDING line (mixed currencies); beyond it the
        request is rejected with ``entry_unbalanced``.
        """
        by_currency: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for ln in plan.lines:
            sign = 1 if ln.debit > 0 else -1
            by_currency[ln.currency] += sign * ln.amount_currency
        currencies = sorted(by_currency)
        if len(currencies) == 1 and by_currency[currencies[0]] != 0 and currencies[0] != FUNCTIONAL_CURRENCY:
            raise ledger_error("entry_unbalanced", f"the entry is not balanced in {currencies[0]}")
        residual = plan.total_debit - plan.total_credit
        if residual == 0:
            return
        if abs(residual) > ROUNDING_TOLERANCE:
            raise ledger_error("entry_unbalanced", f"the entry is not balanced (difference {residual} COP)")
        role = ROUNDING_ROLE if currencies == [FUNCTIONAL_CURRENCY] else FX_ROUNDING_ROLE
        account = self._account_for(ctx, plan.request, plan.journal, PostingLine(role, "DEBIT", abs(residual)), {})
        empty = {slot: None for slot in SLOT_BY_DIMENSION.values()}
        plan.lines.append(
            PlannedLine(
                account=account,
                debit=-residual if residual < 0 else ZERO,
                credit=residual if residual > 0 else ZERO,
                amount_currency=abs(residual),
                currency=FUNCTIONAL_CURRENCY,
                fx_rate=Decimal(1),
                slots=empty,
                memo="Redondeo generado por el motor",
                is_rounding=True,
            )
        )

    def _check_dimensions(self, ctx: TenantContext, plan: Plan) -> None:
        request = plan.request
        matrix = rule_matrix.load_rules(ctx, request.company_id)
        cache: dict[uuid.UUID, DimensionNode | None] = {}
        for i, ln in enumerate(plan.lines):
            for slot, node_id in ln.slots.items():
                if node_id is None:
                    continue
                dimension = DIMENSION_BY_SLOT[slot]
                try:
                    dims.check_line_reference(
                        ctx, request.company_id, dimension, node_id, request.posting_date, cache=cache
                    )
                except ValidationFailed as exc:
                    exc.extra.setdefault("line", i)
                    raise
            if ln.is_rounding:
                continue
            scope = rule_matrix.LineScope(
                account_code=ln.account.code,
                document_type=request.source.type,
                concept=request.purpose,
                journal_code=plan.journal.code,
            )
            modes = rule_matrix.resolve(matrix, scope, list(ln.account.requires_dimensions or []))
            for dimension, mode in modes.items():
                value = ln.slots[SLOT_BY_DIMENSION[dimension]]
                if mode == "REQUIRED" and value is None:
                    raise ledger_error(
                        "dimension_required",
                        f"line {i + 1} (account {ln.account.code}) requires {dimension}",
                        line=i,
                        dimension=dimension,
                        account=ln.account.code,
                    )
                if mode == "FORBIDDEN" and value is not None:
                    raise ledger_error(
                        "dimension_forbidden",
                        f"line {i + 1} (account {ln.account.code}) must not carry {dimension}",
                        line=i,
                        dimension=dimension,
                        account=ln.account.code,
                    )

    # ------------------------------------------------------------------ persistence

    def _existing(self, ctx: TenantContext, request: PostingRequest) -> JournalEntry | None:
        return (
            JournalEntry.objects.filter(
                tenant_id=ctx.tenant_id,
                company_id=request.company_id,
                source_type=request.source.type,
                source_id=request.source.id,
                purpose=request.purpose,
            )
            .order_by("-book__primary", "book__code")
            .first()
        )

    def _number(self, uow: UnitOfWork, company_id: uuid.UUID, book: Book, journal: Journal, day: dt.date) -> str:
        code = f"GL-{book.code}-{journal.code}"[:40]
        try:
            allocated = sequences.allocate(company_id, code, "", on_date=day)
        except SequenceNotFound:
            try:
                with transaction.atomic(using=uow.using):
                    Sequence.objects.create(
                        tenant_id=uow.ctx.tenant_id, company_id=company_id, sequence_code=code, prefix="", padding=6
                    )
            except IntegrityError:
                pass  # created concurrently
            allocated = sequences.allocate(company_id, code, "", on_date=day)
        return f"{journal.code}-{allocated.formatted}"

    def _write_entry(
        self, uow: UnitOfWork, plan: Plan, book: Book, *, reversal_of: JournalEntry | None = None, memo: str = ""
    ) -> JournalEntry:
        ctx = uow.ctx
        request = plan.request
        entry = JournalEntry.objects.create(
            tenant_id=ctx.tenant_id,
            company_id=request.company_id,
            book=book,
            journal=plan.journal,
            period=plan.period,
            number=self._number(uow, request.company_id, book, plan.journal, request.posting_date),
            posting_date=request.posting_date,
            source_type=request.source.type,
            source_id=request.source.id,
            source_number=(request.source.number or "")[:60],
            purpose=request.purpose,
            reversal_of=reversal_of,
            memo=memo,
            branch_id=request.branch_id,
            total_debit=plan.total_debit,
            total_credit=plan.total_credit,
            created_by=ctx.principal_id,
            posted_by=ctx.principal_id,
            channel=ctx.channel.value,
        )
        JournalLine.objects.bulk_create(
            [
                JournalLine(
                    tenant_id=ctx.tenant_id,
                    company_id=request.company_id,
                    entry=entry,
                    book_id=book.id,
                    period_id=plan.period.id,
                    posting_date=request.posting_date,
                    line_no=i + 1,
                    account=ln.account,
                    debit=ln.debit,
                    credit=ln.credit,
                    amount_currency=ln.amount_currency,
                    currency=ln.currency,
                    fx_rate=ln.fx_rate,
                    party_id=ln.party_id,
                    party_kind=ln.party_kind,
                    tax_code=ln.tax_code,
                    item_id=ln.item_id,
                    warehouse_id=ln.warehouse_id,
                    branch_id=request.branch_id,
                    dims_hash=ln.dims_hash,
                    is_rounding=ln.is_rounding,
                    memo=ln.memo,
                    **ln.slots,
                )
                for i, ln in enumerate(plan.lines)
            ]
        )
        self._bump_balances(uow, plan, book)
        uow.emit(
            POSTED_EVENT,
            {
                "entry_id": str(entry.id),
                "number": entry.number,
                "journal": plan.journal.code,
                "posting_date": request.posting_date.isoformat(),
                "total_debit": {"amount": f"{entry.total_debit:.4f}", "currency": FUNCTIONAL_CURRENCY},
                "source": {
                    "type": request.source.type,
                    "id": str(request.source.id),
                    "number": request.source.number,
                },
            },
            company_id=request.company_id,
        )
        audit = services.optional(AuditService)
        if audit is not None:
            audit.record(
                uow,
                AuditEntry(
                    action="ledger.entry.post" if reversal_of is None else "ledger.entry.reverse",
                    resource_type="ledger.journal_entry",
                    resource_id=entry.id,
                    company_id=request.company_id,
                    changes={"number": entry.number, "book": book.code, "source_type": request.source.type},
                    reason=memo,
                ),
            )
        return entry

    def _bump_balances(self, uow: UnitOfWork, plan: Plan, book: Book) -> None:
        """In-line ``ledger_balance`` maintenance under row locks, keys in sorted order."""
        ctx = uow.ctx
        agg: dict[tuple[str, str], list[Any]] = {}
        for ln in plan.lines:
            key = (str(ln.account.id), ln.dims_hash)
            if key not in agg:
                agg[key] = [ln.slots, ZERO, ZERO]
            agg[key][1] += ln.debit
            agg[key][2] += ln.credit
        keys = sorted(agg)
        base = {
            "tenant_id": ctx.tenant_id,
            "company_id": plan.request.company_id,
            "book_id": book.id,
            "period_id": plan.period.id,
        }
        # Ensure every balance row exists (INSERT … ON CONFLICT DO NOTHING), then add the amounts with row-locking
        # UPDATEs in sorted key order (no lost updates, no deadlock between concurrent postings).
        LedgerBalance.objects.using(uow.using).bulk_create(
            [
                LedgerBalance(id=ids.new_id(), account_id=account_id, dims_hash=dh, **base, **agg[(account_id, dh)][0])
                for account_id, dh in keys
            ],
            ignore_conflicts=True,
        )
        for account_id, dh in keys:
            _slots, debit, credit = agg[(account_id, dh)]
            LedgerBalance.objects.using(uow.using).filter(account_id=account_id, dims_hash=dh, **base).update(
                debit=F("debit") + debit, credit=F("credit") + credit
            )

    def _persist(self, uow: UnitOfWork, plan: Plan) -> PostingResult:
        primary: JournalEntry | None = None
        for book in plan.books:
            entry = self._write_entry(uow, plan, book)
            if primary is None:
                primary = entry
        if primary is None:  # pragma: no cover - plan() guarantees at least one book
            raise ValidationFailed("no accounting book configured", pointer="/books", reason="not_found")
        return PostingResult(primary.id, primary.number, primary.total_debit, primary.total_credit)
