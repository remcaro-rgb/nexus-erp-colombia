"""Assertion helpers of the POS tests (read through a bound admin context: forced RLS)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from django.db.models import Sum

from kernel.testing.tenancy import bound
from pos.testing.support import PosEnv


def gl(env: PosEnv, source_id: uuid.UUID) -> dict[str, Decimal]:
    from ledger.models import JournalLine

    with bound(env.world.ctx("admin")):
        rows = (
            JournalLine.objects.filter(company_id=env.company, entry__source_id=source_id, entry__book__code="NIIF")
            .values("account__code")
            .annotate(d=Sum("debit"), c=Sum("credit"))
        )
        return {r["account__code"]: r["d"] - r["c"] for r in rows}


def counts(env: PosEnv) -> dict[str, int]:
    from inventory.models import StockMove
    from kernel.models import OutboxMessage, Sequence
    from ledger.models import JournalEntry
    from pos.models import Ticket

    with bound(env.world.ctx("admin")):
        seq = Sequence.objects.get(id=env.pos_range.id)
        return {
            "entries": JournalEntry.objects.filter(company_id=env.company, book__code="NIIF").count(),
            "moves": StockMove.objects.filter(company_id=env.company).count(),
            "next_number": int(seq.next_number),
            "outbox": OutboxMessage.objects.filter(tenant_id=env.world.tenant_id).count(),
            "posted_tickets": Ticket.objects.filter(company_id=env.company, status="POSTED").count(),
        }


def stock(env: PosEnv, item: Any, presentation: Any = None) -> tuple[Decimal, Decimal]:
    """(canonical on hand in the POS warehouse, packages of ``presentation``)."""
    from inventory.models import PackagedQuant, StockQuant

    with bound(env.world.ctx("admin")):
        on_hand = StockQuant.objects.filter(company_id=env.company, item=item, warehouse=env.warehouse).aggregate(
            s=Sum("on_hand")
        )["s"]
        packages = Decimal(0)
        if presentation is not None:
            packages = PackagedQuant.objects.filter(
                company_id=env.company, item=item, presentation=presentation, warehouse=env.warehouse
            ).aggregate(s=Sum("qty_packages"))["s"] or Decimal(0)
        return Decimal(on_hand or 0), Decimal(packages)


def checkout_params(
    session: Any, lines: list[dict[str, Any]], payments: list[dict[str, Any]], **extra: Any
) -> dict[str, Any]:
    return {
        "client_uuid": str(uuid.uuid4()),
        "session_id": str(session.id),
        "lines": lines,
        "payments": payments,
        **extra,
    }
