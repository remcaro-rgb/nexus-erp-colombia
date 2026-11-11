"""Test kit of the F-PORTAL-CUS tests (synthetic data only, DEV; imported by the part conftest, never by production).

Real components: PostgreSQL with forced RLS, the P04 policy engine + local OIDC issuer (internal ``erp-web`` and
portal ``erp-portal`` bearer tokens, the real invitation → accept onboarding), P09 inventory (stock issued at
invoice posting), P05 ledger (invoice, credit-note and receipt postings), the P10a sales core through its own
API. Contract doubles: P07 ``WorkflowService``, P06 ``TaxService`` (synthetic IVA 19 % — NOT LEGALLY VERIFIED),
audit and notifications.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.core.cache import cache
from rest_framework.test import APIClient

from iam.models import Branch
from iam.oidc.local import LocalIssuer
from iam.testing.world import ADMIN_GRANTS, World
from kernel import clock, ids, services, tenancy
from kernel.interfaces.audit import AuditService
from kernel.interfaces.common import DocumentRef
from kernel.interfaces.dimensions import Dimensions
from kernel.interfaces.notifications import NotificationService
from kernel.interfaces.periods import PeriodService
from kernel.interfaces.posting import PostingService
from kernel.interfaces.stock import Inventory, StockLine, StockService
from kernel.interfaces.tax import TaxService
from kernel.interfaces.workflow import WorkflowService
from kernel.models import Sequence
from kernel.parameters import ParameterProvider
from kernel.testing.fakes import (
    FakeAuditService,
    FakeNotificationService,
    FakeParameterProvider,
    FakeTaxService,
    FakeWorkflowService,
)
from kernel.uow import run_in_unit_of_work

NOW = dt.datetime(2026, 11, 9, 15, 0, tzinfo=dt.UTC)  # Monday 10:00 Bogotá
TODAY = dt.date(2026, 11, 9)
ISSUER = "http://127.0.0.1:8080/auth/realms/erp-dev"
PORTAL_PASSWORD = "dev-only-" + uuid.uuid4().hex  # random per run, never printed
C = "/api/portal/v1/customer"
S = "/api/v1/sales"


class Api:
    """DRF test client with bearer token, ``X-Company-Id`` and a fresh ``Idempotency-Key`` per write."""

    def __init__(self, token: str, company: uuid.UUID | None = None) -> None:
        self.client = APIClient()
        self.token = token
        self.company = company

    def _headers(self, idem: bool, extra: dict[str, str]) -> dict[str, str]:
        h = {"HTTP_AUTHORIZATION": f"Bearer {self.token}"}
        if self.company:
            h["HTTP_X_COMPANY_ID"] = str(self.company)
        if idem:
            h["HTTP_IDEMPOTENCY_KEY"] = str(uuid.uuid4())
        h.update(extra)
        return h

    def get(self, url: str, **params: Any) -> Any:
        return self.client.get(url, params, **self._headers(False, {}))

    def post(self, url: str, data: Any = None, idem: bool = True, **extra: str) -> Any:
        return self.client.post(url, data or {}, format="json", **self._headers(idem, extra))


ACCOUNTS: tuple[tuple[str, str, str, str, bool, list[str], str], ...] = (  # synthetic PUC-like codes (SINTÉTICO)
    ("130505", "Clientes nacionales (SINTÉTICO)", "asset", "debit", True, ["sales."], "AR"),
    ("413595", "Ingresos por ventas (SINTÉTICO)", "income", "credit", False, [], "REVENUE"),
    ("240805", "IVA generado (SINTÉTICO)", "liability", "credit", False, [], "VAT_PAYABLE"),
    ("613595", "Costo de ventas (SINTÉTICO)", "expense", "debit", False, [], "COGS"),
    ("143505", "Mercancías no fabricadas (SINTÉTICO)", "asset", "debit", False, [], "INVENTORY"),
    ("429581", "Ajuste al peso (SINTÉTICO)", "income", "credit", False, [], "ROUNDING"),
    ("417505", "Devoluciones en ventas (SINTÉTICO)", "income", "debit", False, [], "SALES_DISCOUNTS"),
    ("111005", "Bancos (SINTÉTICO)", "asset", "debit", False, [], "BANK"),
    ("135515", "Retención en la fuente a favor (SINTÉTICO)", "asset", "debit", False, [], "WHT_RECEIVABLE"),
    ("421020", "Diferencia en cambio ingreso (SINTÉTICO)", "income", "credit", False, [], "FX_REALIZED_GAIN"),
    ("530525", "Diferencia en cambio gasto (SINTÉTICO)", "expense", "debit", False, [], "FX_REALIZED_LOSS"),
)

ROLES: dict[str, list[str]] = {
    "ventas": ["sales.*.*", "crm.*.*", "inventory.*.read", "workflow.formula.read"],
    "tesoreria": [
        "sales.receipt.*",
        "sales.payment_intent.read",
        "sales.ar_open_item.read",
        "sales.party.read",
        "sales.invoice.read",
    ],
    "formulas": ["workflow.formula.create", "workflow.formula.update", "workflow.formula.read"],
    "formulas_aprobador": ["workflow.formula.approve", "workflow.formula.read"],
    "iam_admin": list(ADMIN_GRANTS),
}
USERS = {
    "vendedor": ["ventas", "formulas"],
    "tesorero": ["tesoreria"],
    "aprobador": ["formulas_aprobador"],
    "admin": ["iam_admin"],
}


@contextmanager
def p10b_services() -> Iterator[dict[str, Any]]:
    """Real ledger + inventory + policy engine; fake workflow / tax / audit / notifications; clock frozen."""
    from inventory import registry as inv_registry
    from inventory.stock.service import SERVICE
    from ledger.registry import implementations as ledger_impls

    cache.clear()
    impls = ledger_impls()
    params = FakeParameterProvider(
        {"tax.rate.IVA19": FakeParameterProvider.synthetic("tax.rate.IVA19", Decimal("0.19"))}
    )
    workflow = FakeWorkflowService()
    audit = FakeAuditService()
    notifications = FakeNotificationService()
    pairs: list[tuple[Callable[..., Any], Any]] = [
        (PostingService, impls[PostingService]),
        (PeriodService, impls[PeriodService]),
        (Dimensions, impls[Dimensions]),
        (AuditService, audit),
        (Inventory, SERVICE),
        (StockService, SERVICE),
        (WorkflowService, workflow),
        (NotificationService, notifications),
        (TaxService, FakeTaxService(params)),
        (ParameterProvider, params),
    ]
    stack = [services.override(interface, impl) for interface, impl in pairs]
    for cm in stack:
        cm.__enter__()
    inv_registry.register_branch_reference()
    inv_registry.register_fields()
    try:
        with clock.frozen(NOW):
            yield {"workflow": workflow, "audit": audit, "notifications": notifications}
    finally:
        for cm in reversed(stack):
            cm.__exit__(None, None, None)
        cache.clear()


@dataclass
class P10bWorld:
    """Company ALFA (BETA for cross-company checks): ledger, fiscal ranges, warehouse, items, internal users."""

    world: World
    issuer: LocalIssuer
    items: dict[str, Any] = field(default_factory=dict)
    customers: dict[str, Any] = field(default_factory=dict)
    warehouse: Any = None

    def company(self, name: str = "ALFA") -> uuid.UUID:
        return self.world.companies[name]

    def ctx(self, user: str | None = None, company: str = "ALFA") -> tenancy.TenantContext:
        principal = self.world.users[user].id if user else None
        return self.world.ctx([company], principal)

    def run(self, fn: Any, user: str | None = None, company: str = "ALFA") -> Any:
        return run_in_unit_of_work(fn, self.ctx(user, company))

    def scope(self, company: str = "ALFA") -> Any:
        return tenancy.scope(self.ctx(None, company))

    def api(self, user: str, company: str = "ALFA") -> Api:
        token = self.issuer.issue(str(self.world.users[user].idp_subject), self.world.tenant_id, "erp-web")
        return Api(token, self.company(company))

    # ------------------------------------------------------------ masters
    def item(self, code: str, price: str, stock: str = "100", cost: str = "400") -> Any:
        from inventory import masters
        from inventory.stock.service import SERVICE
        from sales.pricing.service import set_item_price

        cid = self.company()
        data = {"code": code, "name": f"Producto {code} (SINTÉTICO)", "base_uom": "UND"}
        it = self.run(lambda u: masters.create_item(u, cid, data))
        line = StockLine("1", it.id, self.warehouse.id, Decimal(stock), unit_cost=Decimal(cost))
        doc = DocumentRef("test.opening", ids.new_id(), "SALDO-SYN")
        self.run(lambda u: SERVICE.receive(u, cid, doc, [line], TODAY))
        body = {
            "item_id": str(it.id),
            "currency": "COP",
            "base_price": price,
            "valid_from": "2026-01-01",
            "tax_code": "IVA19",
        }
        self.run(lambda u: set_item_price(u, cid, body), "vendedor")
        self.items[code] = it
        return it

    def customer(self, code: str, nit: str, **extra: Any) -> Any:
        from localization_co.nit import check_digit
        from sales.party.service import create_party

        data = {
            "code": code,
            "legal_name": f"Cliente {code} S.A.S. (SINTÉTICO)",
            "nit": nit,
            "dv": str(check_digit(nit)),
            "person_type": "JURIDICA",
            "roles": ["customer"],
            "email": f"{code.lower()}@example.invalid",
            "payment_terms_days": 30,
            **extra,
        }
        party = self.run(lambda u: create_party(u, self.company(), data), "vendedor")
        self.customers[code] = party
        return party

    def onboard(self, party: Any, username: str, notifications: Any, company: str = "ALFA") -> Api:
        """Invite (admin) → realm user sets a password → login with ``erp-portal`` → accept; the portal API."""
        admin = self.api("admin", company)
        body = {"email": f"{username}@example.invalid", "portal_kind": "customer", "party": {"id": str(party.id)}}
        r = admin.post("/api/v1/iam/external-accounts", body)
        assert r.status_code == 201, r.content
        _nid, request = notifications.sent[-1]
        assert request.template_code == "iam.portal_invitation"
        self.issuer.register(username, PORTAL_PASSWORD, self.world.tenant_id, groups=("portal-customer",))
        portal = Api(self.issuer.login(username, PORTAL_PASSWORD, "erp-portal"))
        r = portal.post("/api/portal/v1/invitations:accept", {"token": str(request.data["invitation_token"])})
        assert r.status_code == 200 and r.json()["external_status"] == "ACTIVE", r.content
        return portal

    # ------------------------------------------------------------ P10a flows through its public API
    def sell(self, party: Any, lines: list[tuple[str, str]], *, post: bool = True) -> dict[str, Any]:
        """Quote → send → order → confirm → invoice (deliver on invoice) → post; returns the posted invoice."""
        api = self.api("vendedor")
        body = {
            "party": str(party.id),
            "document_date": TODAY.isoformat(),
            "lines": [
                {
                    "item_id": str(self.items[c].id),
                    "quantity": q,
                    "tax_code": "IVA19",
                    "warehouse_id": str(self.warehouse.id),
                }
                for c, q in lines
            ],
        }
        q = ok(api.post(f"{S}/quotes", body), 201)
        ok(api.post(f"{S}/quotes/{q['id']}:send"))
        order = ok(api.post(f"{S}/quotes/{q['id']}:to-order"), 201)
        ok(api.post(f"{S}/orders/{order['id']}:confirm"))
        params = {"deliver_on_invoice": True, "warehouse_id": str(self.warehouse.id)}
        inv = ok(api.post(f"{S}/orders/{order['id']}:to-invoice", {"parameters": params}), 201)
        if post:
            inv = ok(api.post(f"{S}/invoices/{inv['id']}:post"))
        inv["order_id"] = order["id"]
        inv["quote_id"] = q["id"]
        return inv

    def credit_note(self, invoice: dict[str, Any], quantity: str = "1") -> dict[str, Any]:
        api = self.api("vendedor")
        body = {
            "original": invoice["id"],
            "reason": "rebaja",
            "reason_text": "descuento comercial (SINTÉTICO)",
            "lines": [{"invoice_line_id": invoice["lines"][0]["id"], "quantity": quantity}],
        }
        note = ok(api.post(f"{S}/credit-notes", body), 201)
        return ok(api.post(f"{S}/credit-notes/{note['id']}:post"))

    def receipt(self, party: Any, open_item_id: str, amount: str) -> dict[str, Any]:
        api = self.api("tesorero")
        body = {
            "party": str(party.id),
            "amount": amount,
            "applications": [{"open_item_id": open_item_id, "amount": amount}],
        }
        rc = ok(api.post(f"{S}/receipts", body), 201)
        return ok(api.post(f"{S}/receipts/{rc['id']}:post"))


def ok(resp: Any, code: int = 200) -> dict[str, Any]:
    assert resp.status_code == code, resp.content
    data: dict[str, Any] = resp.json()
    return data


def _bootstrap(w: P10bWorld, company: str) -> None:
    from ledger import periods, setup
    from ledger.models import Account, AccountDetermination

    cid = w.company(company)

    def fn(u: Any) -> None:
        setup.setup_company(u, cid)
        periods.ensure_year(u, cid, 2026)
        for code, name, typ, nature, control, sources, role in ACCOUNTS:
            acc = Account.objects.create(
                tenant_id=w.world.tenant_id,
                company_id=cid,
                code=code,
                name=name,
                type=typ,
                nature=nature,
                is_control=control,
                control_sources=sources,
            )
            AccountDetermination.objects.create(
                tenant_id=w.world.tenant_id, company_id=cid, role_code=role, account=acc
            )
        for code, prefix in (("FV", "SETT"), ("NC", "NCS"), ("ND", "NDS")):  # synthetic DIAN-like ranges
            Sequence.objects.create(
                tenant_id=w.world.tenant_id,
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

    w.run(fn, company=company)


def make_p10b_world(world: World, issuer: LocalIssuer) -> P10bWorld:
    from inventory import masters

    w = P10bWorld(world, issuer)
    bid = ids.new_id()
    with world.seed():
        Branch.objects.create(
            id=bid,
            tenant_id=world.tenant_id,
            company_id=world.companies["ALFA"],
            code="BOG",
            name="Sucursal Bogotá (SINTÉTICA)",
            is_head_office=True,
        )
    _bootstrap(w, "ALFA")
    cid = w.company()
    w.run(lambda u: masters.create_uom(u, cid, {"code": "UND", "name": "UND", "precision": 0}))
    w.warehouse = w.run(
        lambda u: masters.create_warehouse(u, cid, {"code": "PRIN", "name": "Bodega principal", "branch_id": str(bid)})
    )
    for role, grants in ROLES.items():
        world.add_role(role, grants, max_cls="restricted-pii" if role == "iam_admin" else "confidential")
    for user, roles in USERS.items():
        world.add_user(user, ["ALFA", "BETA"], subject=str(uuid.uuid4()))
        for role in roles:
            world.assign(user, role, "ALFA")
            world.assign(user, role, "BETA")
    return w
