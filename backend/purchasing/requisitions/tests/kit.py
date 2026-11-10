"""Test kit of the P11b tests (synthetic data only, DEV; imported by the part conftests, never by production code).

Real components: PostgreSQL with forced RLS, P04 policy engine + local OIDC issuer (bearer tokens, portal
onboarding), P09 inventory (availability, reservations, transfers), P05 ledger (transfer and receipt postings),
P11a purchasing (RFQ, award, orders, receipts). Contract doubles: P07 ``WorkflowService`` (SoD semantics of the
contract), audit and notifications fakes.
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
from kernel.interfaces.workflow import WorkflowService
from kernel.testing.fakes import FakeAuditService, FakeNotificationService, FakeWorkflowService
from kernel.uow import run_in_unit_of_work

NOW = dt.datetime(2026, 11, 9, 15, 0, tzinfo=dt.UTC)  # Monday 10:00 Bogotá
TODAY = dt.date(2026, 11, 9)
ISSUER = "http://127.0.0.1:8080/auth/realms/erp-dev"


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

    def patch(self, url: str, data: Any, **extra: str) -> Any:
        return self.client.patch(url, data, format="json", **self._headers(True, extra))

    def delete(self, url: str, **extra: str) -> Any:
        return self.client.delete(url, **self._headers(True, extra))


ACCOUNTS: tuple[
    tuple[str, str, str, str, bool, list[str], str], ...
] = (  # synthetic PUC-like codes (SINTÉTICO, not a legal chart of accounts)
    ("143505", "Mercancías no fabricadas (SINTÉTICO)", "asset", "debit", False, [], "INVENTORY"),
    ("143595", "Mercancías en tránsito (SINTÉTICO)", "asset", "debit", False, [], "INVENTORY_IN_TRANSIT"),
    ("229505", "Recepciones por facturar (SINTÉTICO)", "liability", "credit", True, ["purchasing."], "GRNI"),
    ("220505", "Proveedores nacionales (SINTÉTICO)", "liability", "credit", True, ["purchasing."], "AP"),
    ("519595", "Gastos diversos (SINTÉTICO)", "expense", "debit", False, [], "EXPENSE"),
    ("429581", "Ajuste al peso (SINTÉTICO)", "income", "credit", False, [], "ROUNDING"),
)

ROLES: dict[str, list[str]] = {
    "solicitante": [
        "purchasing.internal_requisition.create",
        "purchasing.internal_requisition.read",
        "purchasing.internal_requisition.update",
        "purchasing.internal_requisition.delete",
        "purchasing.internal_requisition.void",
        "purchasing.requisition.create",
        "purchasing.requisition.read",
        "purchasing.requisition.update",
        "inventory.*.read",
    ],
    "jefe_area": [
        "purchasing.internal_requisition.read",
        "purchasing.internal_requisition.approve",
        "purchasing.internal_requisition.post",
        "purchasing.requisition.read",
        "purchasing.requisition.approve",
    ],
    "comprador": ["purchasing.*.*", "inventory.*.read"],
    "aprobador_compras": [
        "purchasing.order.read",
        "purchasing.order.approve",
        "purchasing.order.create",
        "purchasing.rfq.read",
        "purchasing.rfq.approve",
        "purchasing.quote.read",
        "purchasing.supplier_profile_change.read",
        "purchasing.supplier_profile_change.approve",
    ],
    "tesoreria": [
        "purchasing.supplier.read",
        "purchasing.supplier_profile_change.read",
        "purchasing.supplier_profile_change.approve",
    ],
    "almacen": ["inventory.*.*", "purchasing.receipt.*", "purchasing.order.read", "purchasing.shipment_notice.read"],
    "admin_compras": ["purchasing.settings.update", "purchasing.supplier.*"],
    "iam_admin": list(ADMIN_GRANTS),
}
USERS = {
    "sol": ["solicitante"],
    "sol2": ["solicitante"],
    "jefe": ["jefe_area"],
    "comprador": ["comprador"],
    "aprobador": ["aprobador_compras"],
    "tesorero": ["tesoreria"],
    "bodega": ["almacen"],
    "admin": ["admin_compras", "iam_admin"],
    "externo": [],
}


@contextmanager
def p11b_services() -> Iterator[dict[str, Any]]:
    """Real ledger + inventory, fake workflow/audit; clock frozen at :data:`NOW`."""
    from inventory import registry as inv_registry
    from inventory.stock.service import SERVICE
    from ledger.registry import implementations as ledger_impls

    cache.clear()
    impls = ledger_impls()
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


def advance(to: dt.datetime) -> None:
    clock.get_clock().tick(to - clock.now())  # type: ignore[attr-defined]


@dataclass
class P11bWorld:
    """Synthetic company ALFA (BETA for cross-company checks): branches, warehouses, items, users and suppliers."""

    world: World
    issuer: LocalIssuer
    warehouses: dict[str, Any] = field(default_factory=dict)
    items: dict[str, Any] = field(default_factory=dict)
    suppliers: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------ contexts / clients
    def company(self, name: str = "ALFA") -> uuid.UUID:
        return self.world.companies[name]

    def ctx(self, company: str = "ALFA", user: str | None = None) -> tenancy.TenantContext:
        principal = self.world.users[user].id if user else None
        return self.world.ctx([company], principal)

    def run(self, fn: Any, company: str = "ALFA", user: str | None = None) -> Any:
        return run_in_unit_of_work(fn, self.ctx(company, user))

    def scope(self, company: str = "ALFA") -> Any:
        return tenancy.scope(self.ctx(company))

    def api(self, user: str, company: str = "ALFA") -> Any:
        token = self.issuer.issue(str(self.world.users[user].idp_subject), self.world.tenant_id, "erp-web")
        return Api(token, self.company(company))

    # ------------------------------------------------------------ masters
    def warehouse(self, code: str, company: str = "ALFA") -> Any:
        from inventory import masters

        cid = self.company(company)
        bid = self._branches[(company, "BOG")]
        wh = self.run(
            lambda u: masters.create_warehouse(u, cid, {"code": code, "name": f"Bodega {code}", "branch_id": str(bid)}),
            company,
        )
        self.warehouses[code] = wh
        return wh

    def item(self, code: str, company: str = "ALFA", uom: str = "UND") -> Any:
        from inventory import masters

        data = {"code": code, "name": f"Producto {code} (SINTÉTICO)", "base_uom": uom}
        it = self.run(lambda u: masters.create_item(u, self.company(company), data), company)
        self.items[code] = it
        return it

    def receive(self, item: Any, wh: Any, qty: str, cost: str = "1000", company: str = "ALFA") -> Any:
        from inventory.stock.service import SERVICE

        line = StockLine("1", item.id, wh.id, Decimal(qty), unit_cost=Decimal(cost))
        doc = DocumentRef("test.opening", ids.new_id(), "SALDO-SYN")
        return self.run(lambda u: SERVICE.receive(u, self.company(company), doc, [line], TODAY), company)

    def available(self, item: Any, wh: Any) -> Decimal:
        from inventory.stock.service import SERVICE

        with self.scope():
            rows = SERVICE.availability(self.company(), item.id, wh.id)
        return rows[0].available if rows else Decimal(0)

    def supplier(self, code: str, company: str = "ALFA", party_id: uuid.UUID | None = None) -> Any:
        from purchasing.services.suppliers import create_supplier  # test setup through P11a's own service

        body = {
            "code": code,
            "name": f"Proveedor {code} (SINTÉTICO)",
            "nit": f"9001{len(self.suppliers):05d}",
            "rating": "4",
            "payment_terms_days": 30,
            "currency": "COP",
            "party_id": str(party_id or ids.new_id()),
        }
        s = self.run(lambda u: create_supplier(u, self.company(company), body), company, "admin")
        self.suppliers[code] = s
        return s

    _branches: dict[tuple[str, str], uuid.UUID] = field(default_factory=dict)


def _bootstrap_ledger(w: P11bWorld, company: str) -> None:
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

    w.run(fn, company)


def make_p11b_world(world: World, issuer: LocalIssuer) -> P11bWorld:
    from inventory import masters

    w = P11bWorld(world, issuer)
    for company in ("ALFA", "BETA"):
        bid = ids.new_id()
        with world.seed():
            Branch.objects.create(
                id=bid,
                tenant_id=world.tenant_id,
                company_id=world.companies[company],
                code="BOG",
                name="Sucursal Bogotá (SINTÉTICA)",
                is_head_office=True,
            )
        w._branches[(company, "BOG")] = bid
        _bootstrap_ledger(w, company)
        w.run(
            lambda u, c=company: masters.create_uom(
                u, world.companies[c], {"code": "UND", "name": "UND", "precision": 0}
            ),
            company,
        )
    for role, grants in ROLES.items():
        world.add_role(role, grants, max_cls="restricted-pii" if role == "iam_admin" else "confidential")
    for user, roles in USERS.items():
        world.add_user(user, ["ALFA", "BETA"], subject=str(uuid.uuid4()))
        for role in roles:
            world.assign(user, role, "ALFA")
            world.assign(user, role, "BETA")
    return w


# --------------------------------------------------------------------------- P11a flows through its public API

PUR = "/api/v1/purchasing"


def rfq_sent(
    w: P11bWorld, items: list[tuple[Any, str]], vendors: list[Any], *, deadline: dt.datetime | None = None
) -> dict[str, Any]:
    """A SENT RFQ (buyer ``comprador``) for ``items`` = [(item, qty)] inviting ``vendors``."""
    api = w.api("comprador")
    body = {
        "document_date": clock.business_date().isoformat(),
        "deadline": (deadline or clock.now() + dt.timedelta(days=2)).isoformat(),
        "currency": "COP",
        "invited_vendors": [str(v.id) for v in vendors],
        "lines": [
            {"line_no": n, "item_id": str(it.id), "description": it.name, "quantity": {"value": qty, "uom": "UND"}}
            for n, (it, qty) in enumerate(items, start=1)
        ],
    }
    r = api.post(f"{PUR}/rfqs", body)
    assert r.status_code == 201, r.content
    rfq: dict[str, Any] = r.json()
    r = api.post(f"{PUR}/rfqs/{rfq['id']}:send")
    assert r.status_code == 200, r.content
    return dict(r.json())


def quote_lines(items: list[tuple[Any, str]], prices: list[str]) -> list[dict[str, Any]]:
    return [
        {"line_no": n, "quantity": {"value": qty, "uom": "UND"}, "unit_price": {"amount": p, "currency": "COP"}}
        for n, ((_it, qty), p) in enumerate(zip(items, prices, strict=True), start=1)
    ]


def award_rfq(w: P11bWorld, rfq_id: str) -> dict[str, Any]:
    """Close after the deadline, evaluate, award the recommendation (comprador) and approve (aprobador, SoD)."""
    api = w.api("comprador")
    advance(dt.datetime.fromisoformat(api.get(f"{PUR}/rfqs/{rfq_id}").json()["deadline"]) + dt.timedelta(minutes=1))
    for action in ("close", "evaluate"):
        r = api.post(f"{PUR}/rfqs/{rfq_id}:{action}")
        assert r.status_code == 200, r.content
    comparison: dict[str, Any] = api.get(f"{PUR}/rfqs/{rfq_id}/comparison").json()
    r = api.post(f"{PUR}/rfqs/{rfq_id}:award", {"parameters": {"quote_id": comparison["recommended_quote_id"]}})
    assert r.status_code == 200, r.content
    r = w.api("aprobador").post(f"{PUR}/rfqs/{rfq_id}:approve-award", {"reason": "ok"})
    assert r.status_code == 200, r.content
    return comparison


def approved_price(w: P11bWorld, item: Any, vendor: Any, price: str) -> None:
    """A valid D-011 vendor price: an approved award of ``vendor`` for ``item`` at ``price``."""
    items = [(item, "1")]
    rfq = rfq_sent(w, items, [vendor])
    r = w.api("comprador").post(
        f"{PUR}/rfqs/{rfq['id']}/quotes",
        {"party": {"id": str(vendor.id)}, "currency": "COP", "lead_time_days": 3, "lines": quote_lines(items, [price])},
    )
    assert r.status_code == 201, r.content
    award_rfq(w, rfq["id"])


def order_sent(w: P11bWorld, order_id: str) -> dict[str, Any]:
    """DRAFT → SUBMITTED (comprador) → APPROVED (aprobador) → SENT."""
    r = w.api("comprador").post(f"{PUR}/orders/{order_id}:submit")
    assert r.status_code == 200, r.content
    if r.json()["status"] != "APPROVED":
        r = w.api("aprobador").post(f"{PUR}/orders/{order_id}:approve", {"reason": "ok"})
        assert r.status_code == 200, r.content
    r = w.api("comprador").post(f"{PUR}/orders/{order_id}:send")
    assert r.status_code == 200, r.content
    return dict(r.json())


def receive_order(w: P11bWorld, order: dict[str, Any], warehouse: Any, qty: str, line_no: int = 1) -> None:
    api = w.api("bodega")
    line = next(ln for ln in order["lines"] if ln["line_no"] == line_no)
    r = api.post(
        f"{PUR}/receipts",
        {
            "order": {"id": order["id"]},
            "warehouse_id": str(warehouse.id),
            "lines": [{"order_line_id": line["id"], "quantity": {"value": qty, "uom": "UND"}}],
        },
    )
    assert r.status_code == 201, r.content
    r = api.post(f"{PUR}/receipts/{r.json()['id']}:validate")
    assert r.status_code == 200, r.content


# --------------------------------------------------------------------------- supplier portal identities (D-018)

PORTAL_PASSWORD = "dev-only-" + uuid.uuid4().hex  # random per run, never printed


def onboard_supplier(w: P11bWorld, notifications: Any, supplier: Any, username: str, company: str = "ALFA") -> Api:
    """Invite (admin) → realm user sets a password → login with ``erp-portal`` → accept; returns the portal API."""
    admin = w.api("admin", company)
    r = admin.post(
        "/api/v1/iam/external-accounts",
        {"email": f"{username}@example.invalid", "portal_kind": "supplier", "party": {"id": str(supplier.party_id)}},
    )
    assert r.status_code == 201, r.content
    _nid, request = notifications.sent[-1]
    assert request.template_code == "iam.portal_invitation"
    w.issuer.register(username, PORTAL_PASSWORD, w.world.tenant_id, groups=("portal-supplier",))
    portal = Api(w.issuer.login(username, PORTAL_PASSWORD, "erp-portal"))
    r = portal.post("/api/portal/v1/invitations:accept", {"token": str(request.data["invitation_token"])})
    assert r.status_code == 200 and r.json()["external_status"] == "ACTIVE", r.content
    return portal
