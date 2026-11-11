"""Shared world of the G2 gate scenarios (gate-owned, wave-05 integrator; synthetic data, DEV only).

The P11b kit world (``purchasing.requisitions.tests.kit``) is the base: PostgreSQL with forced RLS, the real P04
policy engine with local OIDC bearer tokens, real P09 inventory, real P05 ledger, real P11a/P11b purchasing. This
module adds what the cross-module scenarios S-PUR / S-TRE / S-INV need on top of it, all real implementations:

* P16 treasury (bank account + fund, RC/CE vouchers, statements) with P11's ``ApOpenItems`` adapter and P10's real
  ``ArOpenItems``; a role ``tesoreria_g2`` for the treasury users;
* synthetic PUC-like accounts for VAT, withholdings, bank, AR, capital, price variance, COGS, consumption
  (all labelled SINTÉTICO, not a legal chart of accounts);
* ``TaxService``: the kernel contract double over labelled synthetic rates (``tax.rate.IVA19`` 19 %,
  ``tax.rate.RTE25`` 2.5 %; NOT LEGALLY VERIFIED). Official rates are EXTERNAL (CLAUDE.md), so no real rate may be
  invented here; the P06 engine itself is covered by its own tests and by the P22 scenarios.

P07 workflow / audit / notifications stay the contract doubles the P11b kit installs (SoD semantics of the
contract); every approval threshold used here is left below the P07 routing limit.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from decimal import Decimal
from typing import Any

from django.core.cache import cache
from django.db.models import Sum

from iam.oidc import verifier
from iam.oidc.local import LocalIssuer
from iam.testing.world import make_world
from kernel import services
from purchasing.requisitions.tests.kit import ISSUER, TODAY, P11bWorld, make_p11b_world, p11b_services

D = Decimal
TRE = "/api/v1/treasury"
PUR = "/api/v1/purchasing"

#: extra synthetic accounts (code, name, type, nature, control sources, role)
EXTRA_ACCOUNTS: tuple[tuple[str, str, str, str, list[str], str], ...] = (
    ("111005", "Bancos cuenta corriente (SINTÉTICO)", "asset", "debit", [], ""),
    ("130505", "Clientes nacionales (SINTÉTICO)", "asset", "debit", ["sales.", "treasury."], "AR"),
    ("280505", "Anticipos de clientes (SINTÉTICO)", "liability", "credit", [], "CUSTOMER_ADVANCE"),
    ("133005", "Anticipos a proveedores (SINTÉTICO)", "asset", "debit", [], "VENDOR_ADVANCE"),
    ("135515", "Retención en la fuente a favor (SINTÉTICO)", "asset", "debit", [], "WHT_RECEIVABLE"),
    ("236540", "Retención en la fuente por pagar (SINTÉTICO)", "liability", "credit", [], "WHT_PAYABLE"),
    ("240810", "IVA descontable (SINTÉTICO)", "asset", "debit", [], "VAT_RECOVERABLE"),
    ("613595", "Diferencia en precio de compra (SINTÉTICO)", "expense", "debit", [], "PURCHASE_PRICE_VARIANCE"),
    ("311505", "Aportes sociales (SINTÉTICO)", "equity", "credit", [], "CAPITAL"),
)
SYNTHETIC_RATES = {"IVA19": D("0.19"), "RTE25": D("0.025")}

TREASURY_GRANTS = [
    "treasury.*",
    "sales.receipt.post",
    "sales.party.*",
    "purchasing.supplier.read",
    "ledger.dimension.*",
    "workflow.approval.approve",
]


@contextmanager
def gate_services() -> Iterator[dict[str, Any]]:
    """The P11b kit services + synthetic tax/parameters + the real AR/AP open-item adapters."""
    from kernel.interfaces.ar import ArOpenItems
    from kernel.interfaces.tax import TaxService
    from kernel.parameters import ParameterProvider
    from kernel.testing.fakes import FakeParameterProvider, FakeTaxService
    from sales.services.ar import SalesArOpenItems
    from treasury.interfaces import ApOpenItems
    from treasury.vouchers.ap import PurchasingApOpenItems

    params = FakeParameterProvider(
        {
            f"tax.rate.{code}": FakeParameterProvider.synthetic(f"tax.rate.{code}", rate)
            for code, rate in SYNTHETIC_RATES.items()
        }
    )
    overrides: list[tuple[Callable[..., Any], Any]] = [
        (TaxService, FakeTaxService(params)),
        (ParameterProvider, params),
        (ArOpenItems, SalesArOpenItems()),
        (ApOpenItems, PurchasingApOpenItems()),
    ]
    with p11b_services() as fakes, ExitStack() as stack:
        for interface, impl in overrides:
            stack.enter_context(services.override(interface, impl))
        yield fakes


@contextmanager
def gate_world() -> Iterator[P11bWorld]:
    issuer = LocalIssuer(ISSUER)
    verifier.set_key_provider(issuer, ISSUER)
    cache.clear()
    try:
        with gate_services():
            w = make_p11b_world(make_world(), issuer)
            _boot_treasury(w)
            yield w
    finally:
        verifier.set_key_provider(None)
        cache.clear()


def _boot_treasury(w: P11bWorld) -> None:
    from ledger.models import Account, AccountDetermination
    from treasury.services.common import configure
    from treasury.services.masters import create_bank_account

    world = w.world
    world.add_role("tesoreria_g2", TREASURY_GRANTS, max_cls="confidential")
    world.assign("tesorero", "tesoreria_g2", "ALFA")
    # a third purchasing approver: an award approver creates the PO and the buyer submits it, so SoD (D-023)
    # needs someone else to approve
    world.add_role("aprobador_po_g2", ["purchasing.order.read", "purchasing.order.approve"], max_cls="confidential")
    world.assign("jefe", "aprobador_po_g2", "ALFA")
    cid = w.company()

    def fn(u: Any) -> None:
        accounts: dict[str, Any] = {}
        for code, name, typ, nature, sources, role in EXTRA_ACCOUNTS:
            accounts[code] = Account.objects.create(
                tenant_id=world.tenant_id,
                company_id=cid,
                code=code,
                name=name,
                type=typ,
                nature=nature,
                is_control=bool(sources),
                control_sources=sources,
            )
            if role:
                AccountDetermination.objects.create(
                    tenant_id=world.tenant_id, company_id=cid, role_code=role, account=accounts[code]
                )
        # the vendor control account is shared by purchasing (invoices) and treasury (CE payments)
        Account.objects.filter(company_id=cid, code="220505").update(control_sources=["purchasing.", "treasury."])
        bank = create_bank_account(
            u,
            cid,
            {
                "code": "BCO-1",
                "name": "Banco sintético cuenta corriente",
                "bank_code": "SYN-BANK",
                "account_number": "000123456789",
                "currency": "COP",
                "account_id": str(accounts["111005"].id),
            },
        )
        w.warehouses["__bank__"] = bank
        # CE above COP 0 route to P07 by default; the scenarios pay below the routing limit (approval is covered by
        # T-TRE-CE-04 with the real DbWorkflowService)
        configure(u, cid, ce_approval_threshold=None)

    w.run(fn, user="tesorero")


def bank(w: P11bWorld) -> Any:
    return w.warehouses["__bank__"]


def fund_id(w: P11bWorld) -> str:
    return str(bank(w).fund.id)


def money(amount: str) -> dict[str, str]:
    return {"amount": amount, "currency": "COP"}


def gl(w: P11bWorld) -> dict[str, Decimal]:
    """Primary-book balance (debit − credit) per account code of company ALFA."""
    from ledger.models import JournalLine

    with w.scope():
        rows = (
            JournalLine.objects.filter(company_id=w.company(), entry__book__primary=True)
            .values("account__code")
            .annotate(d=Sum("debit"), c=Sum("credit"))
        )
        return {r["account__code"]: r["d"] - r["c"] for r in rows}


def capital_in(w: P11bWorld, amount: str) -> dict[str, Any]:
    """A posted capital-contribution RC so the bank fund has money to pay with (treasury concept CAPITAL)."""
    from treasury.services.masters import create_concept

    concept = w.run(
        lambda u: create_concept(
            u,
            w.company(),
            {"code": "APORTE", "name": "Aporte de capital (SINTÉTICO)", "direction": "IN", "account_role": "CAPITAL"},
            extended=True,
        ),
        user="tesorero",
    )
    api = w.api("tesorero")
    r = api.post(
        f"{TRE}/receipts",
        {
            "document_date": TODAY.isoformat(),
            "fund_id": fund_id(w),
            "payment_method": "TRANSFER",
            "currency": "COP",
            "lines": [{"line_no": 1, "concept_id": str(concept.id), "applied_amount": money(amount)}],
        },
    )
    assert r.status_code == 201, r.content
    posted = api.post(f"{TRE}/receipts/{r.json()['id']}:post")
    assert posted.status_code == 200, posted.content
    return dict(posted.json())


#: synthetic sales accounts of P10 (code, name, type, nature, role); AR / bank / withholdings come from above
SALES_ACCOUNTS: tuple[tuple[str, str, str, str, str], ...] = (
    ("413595", "Ingresos por ventas (SINTÉTICO)", "income", "credit", "REVENUE"),
    ("240805", "IVA generado (SINTÉTICO)", "liability", "credit", "VAT_PAYABLE"),
    ("613510", "Costo de ventas (SINTÉTICO)", "expense", "debit", "COGS"),
    ("417505", "Devoluciones en ventas (SINTÉTICO)", "income", "debit", "SALES_DISCOUNTS"),
)
SALES_GRANTS = ["sales.*", "inventory.*.read"]


def boot_sales(w: P11bWorld) -> None:
    """P10 sales on the gate world: accounts, synthetic fiscal ranges (SETT…), user ``vendedor``."""
    import datetime as dt
    import uuid

    from kernel.models import Sequence
    from ledger.models import Account, AccountDetermination

    world = w.world
    world.add_role("ventas_g2", SALES_GRANTS, max_cls="restricted-pii")
    world.add_user("vendedor", ["ALFA"], subject=str(uuid.uuid4()))
    world.assign("vendedor", "ventas_g2", "ALFA")
    cid = w.company()

    def fn(u: Any) -> None:
        for code, name, typ, nature, role in SALES_ACCOUNTS:
            acc = Account.objects.create(
                tenant_id=world.tenant_id, company_id=cid, code=code, name=name, type=typ, nature=nature
            )
            AccountDetermination.objects.create(tenant_id=world.tenant_id, company_id=cid, role_code=role, account=acc)
        for code, prefix in (("FV", "SETT"), ("NC", "NCS"), ("ND", "NDS")):  # synthetic DIAN-like ranges
            Sequence.objects.create(
                tenant_id=world.tenant_id,
                company_id=cid,
                sequence_code=code,
                prefix=prefix,
                is_fiscal=True,
                range_from=1,
                range_to=5000,
                valid_from=dt.date(2026, 1, 1),
                valid_to=dt.date(2027, 12, 31),
                resolution_number="SINTETICA-0001",
            )

    w.run(fn)


def customer(w: P11bWorld, code: str) -> Any:
    from localization_co.nit import check_digit
    from sales.party.service import create_party

    nit = "900777001"
    data = {
        "code": code,
        "legal_name": f"Cliente {code} S.A.S. (SINTÉTICO)",
        "nit": nit,
        "dv": str(check_digit(nit)),
        "person_type": "JURIDICA",
        "roles": ["customer"],
    }
    return w.run(lambda u: create_party(u, w.company(), data), user="vendedor")


def base_price(w: P11bWorld, item: Any, price: str) -> Any:
    from sales.pricing.service import set_item_price

    data = {"item_id": str(item.id), "currency": "COP", "base_price": price, "valid_from": "2026-01-01"}
    return w.run(lambda u: set_item_price(u, w.company(), data), user="vendedor")
