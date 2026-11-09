"""Gap-free numbering (C2-3, C1-12, invariant 9).

Numbers are allocated only inside a server unit of work with
``SELECT ... FOR UPDATE`` on the sequence row: a rollback returns the number,
so committed numbers have no gaps. Fiscal sequences enforce the DIAN
numbering-resolution range and validity (external data entered by the tenant)
and emit ``kernel.sequence.threshold.v1`` at 80 % and 95 % consumption.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from decimal import Decimal

from kernel import clock, dates, flags
from kernel.errors import FeatureDisabled, SequenceExhausted, SequenceNotFound, SequenceNotValid
from kernel.models import Sequence
from kernel.uow import require_uow

THRESHOLDS = (Decimal("0.80"), Decimal("0.95"))
THRESHOLD_EVENT = "kernel.sequence.threshold.v1"
EXHAUSTED_EVENT = "kernel.sequence.exhausted.v1"


@dataclass(frozen=True, slots=True)
class AllocatedNumber:
    sequence_id: uuid.UUID
    number: int
    prefix: str
    formatted: str
    resolution_number: str
    is_fiscal: bool


def _format(seq: Sequence, number: int) -> str:
    digits = str(number).zfill(seq.padding) if seq.padding else str(number)
    return f"{seq.prefix}{digits}"


def _usage(seq: Sequence, number: int) -> Decimal | None:
    if seq.range_from is None or seq.range_to is None:
        return None
    size = seq.range_to - seq.range_from + 1
    return Decimal(number - seq.range_from + 1) / Decimal(size)


def allocate(
    company_id: uuid.UUID,
    sequence_code: str,
    prefix: str = "",
    on_date: dt.date | None = None,
    timezone: str | None = None,
) -> AllocatedNumber:
    """Allocate the next number of ``(tenant, company, code, prefix)``.

    Requires an active unit of work. Raises :class:`SequenceNotFound`,
    :class:`SequenceNotValid` (inactive / outside validity) or
    :class:`SequenceExhausted` (beyond ``range_to``).
    """
    uow = require_uow()
    ctx = uow.ctx
    ctx.require_company(company_id)
    day = on_date or clock.business_date(timezone)
    seq = (
        Sequence.objects.using(uow.using)
        .select_for_update()
        .filter(
            tenant_id=ctx.tenant_id,
            company_id=company_id,
            sequence_code=sequence_code,
            prefix=prefix,
        )
        .first()
    )
    if seq is None:
        raise SequenceNotFound(f"sequence {sequence_code}/{prefix or '-'} not configured")
    if not seq.active or not dates.is_effective(seq.valid_from, seq.valid_to, day):
        raise SequenceNotValid(f"sequence {sequence_code}/{prefix or '-'} not valid on {day}")
    number = seq.next_number
    if seq.range_from is not None and number < seq.range_from:
        number = seq.range_from
    if seq.range_to is not None and number > seq.range_to:
        uow.emit(
            EXHAUSTED_EVENT,
            {"sequence_id": str(seq.id), "sequence_code": sequence_code, "prefix": prefix},
            company_id=company_id,
        )
        raise SequenceExhausted(f"sequence {sequence_code}/{prefix or '-'} exhausted")
    seq.next_number = number + 1
    update = ["next_number"]
    usage = _usage(seq, number)
    if usage is not None:
        for threshold, attr in zip(THRESHOLDS, ("alert_80_sent", "alert_95_sent"), strict=True):
            if usage >= threshold and not getattr(seq, attr):
                setattr(seq, attr, True)
                update.append(attr)
                uow.emit(
                    THRESHOLD_EVENT,
                    {
                        "sequence_id": str(seq.id),
                        "sequence_code": sequence_code,
                        "prefix": prefix,
                        "threshold": str(threshold),
                        "number": number,
                        "range_to": seq.range_to,
                    },
                    company_id=company_id,
                )
    seq.save(update_fields=update)
    return AllocatedNumber(
        sequence_id=seq.id,
        number=number,
        prefix=prefix,
        formatted=_format(seq, number),
        resolution_number=seq.resolution_number,
        is_fiscal=seq.is_fiscal,
    )


def allocate_terminal_block(company_id: uuid.UUID, sequence_code: str, size: int) -> None:
    """Extension point for per-terminal block pre-allocation (ADR-015).

    Only for the ``[A]`` flag ``pos_offline_fiscal_selling``, off by default and
    gated by EG-13. This edition ships no implementation: the call always fails
    closed.
    """
    ctx = require_uow().ctx
    if not flags.is_enabled(flags.POS_OFFLINE_FISCAL_SELLING, ctx.tenant_id, company_id):
        raise FeatureDisabled("pos_offline_fiscal_selling is disabled (ADR-015, EG-13)")
    raise FeatureDisabled("terminal block allocation is not available in this edition")
