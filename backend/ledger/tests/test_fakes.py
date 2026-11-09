"""The consumer double raises the same dimension error codes as the real engine."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from kernel import ids
from kernel.errors import DomainError
from kernel.interfaces.common import DimensionValues, DocumentRef
from kernel.interfaces.posting import PostingLine, PostingRequest
from kernel.testing.tenancy import make_context
from kernel.uow import UnitOfWork
from ledger.testing.fakes import FakeLedger, FakeNode


def test_fake_ledger_dimension_errors():
    ctx = make_context()
    company = next(iter(ctx.company_ids))
    ledger = FakeLedger()
    group = ledger.add_node(FakeNode(ids.new_id(), company, "cost_center", "G", "AGRUPADOR"))
    leaf = ledger.add_node(FakeNode(ids.new_id(), company, "cost_center", "G-1"))
    uow = UnitOfWork(ctx=ctx)

    def req(node):
        dims = DimensionValues(cost_center_id=node.id if node else None)
        return PostingRequest(
            company,
            dt.date(2026, 11, 9),
            DocumentRef("x.doc", ids.new_id()),
            "p",
            (PostingLine("EXPENSE", "DEBIT", Decimal(1), dimensions=dims), PostingLine("CASH", "CREDIT", Decimal(1))),
        )

    with pytest.raises(DomainError) as exc:
        ledger.post(uow, req(group))
    assert exc.value.code == "dimension_node_not_postable"
    ledger.rules[("EXPENSE", "cost_center")] = "REQUIRED"
    with pytest.raises(DomainError) as exc:
        ledger.simulate(uow, req(None))
    assert exc.value.code == "dimension_required"
    assert ledger.post(uow, req(leaf)).total_debit_cop == Decimal(1)
