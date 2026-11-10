"""S2-style receipt and vendor invoice posted through the REAL P05 PostingService (synthetic PUC accounts).

Proves the purchasing account roles (INVENTORY, GRNI, AP, VAT_RECOVERABLE, WHT_PAYABLE, PURCHASE_PRICE_VARIANCE)
post balanced, immutable entries in the same unit of work as the documents, with AP/GRNI as control accounts
reserved to purchasing sources, and that a closed period blocks the whole document.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from django.db.models import Sum

from kernel import clock
from kernel.interfaces.posting import PostingService
from kernel.testing.tenancy import bound
from kernel.uow import run_in_unit_of_work
from ledger import periods, setup
from ledger.dimensions import service as dims
from ledger.models import Account, AccountDetermination, JournalEntry, JournalLine, Period
from ledger.registry import implementations
from purchasing.testing.support import NOW, ApiClient, World, fake_services, make_world
from purchasing.tests.test_procure_to_pay import invoice, receive, setup_order

ACCOUNTS = (  # synthetic PUC-like codes (SINTÉTICO, not a legal chart of accounts)
    ("143505", "Inventario mercancías (SINTÉTICO)", "asset", "debit", False, [], "INVENTORY"),
    ("229505", "Recepciones por facturar (SINTÉTICO)", "liability", "credit", True, ["purchasing."], "GRNI"),
    ("220505", "Proveedores nacionales (SINTÉTICO)", "liability", "credit", True, ["purchasing."], "AP"),
    ("240810", "IVA descontable (SINTÉTICO)", "asset", "debit", False, [], "VAT_RECOVERABLE"),
    ("236540", "Retención en la fuente por pagar (SINTÉTICO)", "liability", "credit", False, [], "WHT_PAYABLE"),
    ("613595", "Diferencia en precio de compra (SINTÉTICO)", "expense", "debit", False, [], "PURCHASE_PRICE_VARIANCE"),
    ("519595", "Gastos diversos (SINTÉTICO)", "expense", "debit", False, [], "EXPENSE"),
    ("429581", "Ajuste al peso (SINTÉTICO)", "income", "credit", False, [], "ROUNDING"),
)


@pytest.fixture
def ledger_world(db: Any, settings: Any) -> Iterator[World]:
    real = implementations()[PostingService]
    with clock.frozen(NOW), fake_services(real_posting=real) as fakes:
        world = make_world(fakes)
        settings.ERP_TENANT_CONTEXT_RESOLVER = "purchasing.testing.support.resolve_context"

        def boot(u: Any) -> None:
            setup.setup_company(u, world.company)
            periods.ensure_year(u, world.company, 2026)
            for code, name, typ, nature, control, sources, role in ACCOUNTS:
                acc = Account.objects.create(
                    tenant_id=world.tenant_id,
                    company_id=world.company,
                    code=code,
                    name=name,
                    type=typ,
                    nature=nature,
                    is_control=control,
                    control_sources=sources,
                )
                AccountDetermination.objects.create(
                    tenant_id=world.tenant_id, company_id=world.company, role_code=role, account=acc
                )

        run_in_unit_of_work(boot, world.ctx("admin"))
        node = run_in_unit_of_work(
            lambda u: dims.create_node(
                u,
                world.company,
                "cost_center",
                dims.NodeInput(
                    code="CC-PROD", description="Producción (SINTÉTICO)", node_type="DETALLE", valid_from="2026-01-01"
                ),
            ),
            world.ctx("admin"),
        )
        world.suppliers["__cost_center__"] = node
        yield world


def balances(world: World) -> dict[str, Decimal]:
    with bound(world.ctx("admin")):
        rows = (
            JournalLine.objects.filter(company_id=world.company, entry__book__code="NIIF")
            .values("account__code")
            .annotate(d=Sum("debit"), c=Sum("credit"))
        )
        return {r["account__code"]: r["d"] - r["c"] for r in rows}


def test_T_PUR_CYCLE_05_receipt_and_invoice_post_through_the_real_ledger(ledger_world: World) -> None:
    world = ledger_world
    api = ApiClient(world)
    cost_center = world.suppliers["__cost_center__"].id
    order = setup_order(api, world, cost_center=cost_center)
    rec = receive(api, order, "100")
    validated = api.as_("warehouse").post(f"receipts/{rec['id']}:validate")
    assert validated.status_code == 200, validated.content
    inv = invoice(api, order, "FV-REAL-001", "100", "3030")  # +1 % within the 2 % tolerance
    accountant = api.as_("accountant")
    assert accountant.post(f"vendor-invoices/{inv['id']}:match").json()["match_status"] == "MATCHED"
    posted = accountant.post(f"vendor-invoices/{inv['id']}:post")
    assert posted.status_code == 200, posted.content
    b = balances(world)
    # Receipt: Dr 143505 300,000 / Cr 229505 300,000. Invoice: Dr 229505 300,000, Dr 613595 3,000,
    # Dr 240810 57,570 / Cr 236540 7,575, Cr 220505 352,995.
    assert b["143505"] == Decimal("300000.0000")
    assert b["229505"] == Decimal("0.0000")  # GRNI cleared
    assert b["613595"] == Decimal("3000.0000")
    assert b["240810"] == Decimal("57570.0000")
    assert b["236540"] == Decimal("-7575.0000")
    assert b["220505"] == Decimal("-352995.0000")
    assert sum(b.values()) == 0
    with bound(world.ctx("admin")):
        sources = set(JournalEntry.objects.filter(company_id=world.company).values_list("source_type", flat=True))
        # [DIM] the PO line's cost center rides on the receipt and invoice lines into the journal.
        dims_on_lines = set(
            JournalLine.objects.filter(company_id=world.company, account__code__in=["143505", "613595"]).values_list(
                "cost_center_id", flat=True
            )
        )
    assert {"purchasing.receipt", "purchasing.vendor_invoice"} <= sources
    assert dims_on_lines == {cost_center}
    # AP subledger = GL AP.
    items = accountant.get("ap/open-items").json()["items"]
    assert -b["220505"] == sum(Decimal(i["balance_cop"]["amount"]) for i in items)


def test_closed_period_blocks_the_receipt_and_rolls_everything_back(ledger_world: World) -> None:
    world = ledger_world
    api = ApiClient(world)
    order = setup_order(api, world, cost_center=world.suppliers["__cost_center__"].id)
    rec = receive(api, order, "10")
    with bound(world.ctx("admin")):
        Period.objects.filter(
            company_id=world.company, start__lte=dt.date(2026, 11, 10), end__gte=dt.date(2026, 11, 10)
        ).update(status="CLOSED")
    failed = api.as_("warehouse").post(f"receipts/{rec['id']}:validate")
    assert failed.status_code in (409, 422) and failed.json()["code"] == "period_closed"
    assert api.get(f"receipts/{rec['id']}").json()["receipt_status"] == "DRAFT"
    # The DB rolled back as one unit: no journal line, no PO progress. (The in-memory FakeInventory is not
    # transactional; the real P09 stock ledger writes in this same database transaction.)
    assert balances(world) == {}
    assert api.as_("buyer").get(f"orders/{order['id']}").json()["lines"][0]["received_qty"] == "0.000000"
