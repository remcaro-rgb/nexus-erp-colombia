"""Real-data world of the P22c leak/IDOR suite (T-X-QA-03), DEV/test only, synthetic data.

Tenant T1 = the G2 gate world (tests/gates/g2_world.py: P04 policy engine + local OIDC, P09 inventory, P05 ledger,
P10 sales, P11 purchasing, P16 treasury) with real documents created through the public API or the owners' services:
items/presentations/warehouses, RFQs with quotes and purchase orders for two suppliers, receipts, recibos de caja,
a sales order/invoice for a customer, cost centers. Tenant T2 is a second synthetic tenant with an all-powerful user.
Portal identities (D-018/D-033): suppliers A and B, a customer, an employee — onboarded through the real invitation
flow (P04) with the local issuer.

Principals (all LocalIssuer tokens, never printed):
* ``admin``    T1, member of ALFA+BETA, every grant (positive control: proves each object is real and reachable);
* ``beta``     T1, member of BETA only, every grant (cross-company);
* ``nogrant``  T1, member of ALFA, only ``iam.me.read`` (BFLA);
* ``t2admin``  T2, every grant in T2's own companies (cross-tenant BOLA);
* portal ``supA``, ``supB``, ``cus``, ``emp``.
"""

from __future__ import annotations

import datetime as dt
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT / "tests" / "gates",):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

#: every action of the permission catalog (deny-by-default engine, fnmatch grants)
ALL_GRANTS = ["*", "*.*", "*.*.*", "*.*.*.*"]
SALES = "/api/v1/sales"
TRE = "/api/v1/treasury"
INV = "/api/v1/inventory"


@dataclass
class IdorWorld:
    w: Any  # P11bWorld of tenant T1
    t2_tenant: uuid.UUID
    t2_companies: dict[str, uuid.UUID]
    tokens: dict[str, str] = field(default_factory=dict)
    companies: dict[str, uuid.UUID] = field(default_factory=dict)  # principal -> X-Company-Id
    portal: dict[str, Any] = field(default_factory=dict)  # portal principal -> kit Api
    party_ids: dict[str, str] = field(default_factory=dict)  # portal principal -> party id


def _ok(r: Any, code: int = 200) -> dict[str, Any]:
    assert r.status_code == code, (r.status_code, r.content[:600])
    return dict(r.json())


def _seed_t1(w: Any) -> dict[str, Any]:
    from g2_world import PUR, base_price, boot_sales, capital_in, customer
    from purchasing.requisitions.tests.kit import TODAY, order_sent, quote_lines, receive_order, rfq_sent

    boot_sales(w)
    wh = w.warehouse("PRIN")
    item = w.item("ARROZ-SYN")
    item2 = w.item("AZUCAR-SYN")
    w.receive(item, wh, "50", cost="2000")
    w.receive(item2, wh, "40", cost="1500")
    sup_a = w.supplier("PROV-A")
    sup_b = w.supplier("PROV-B")

    # purchasing: an RFQ inviting both suppliers with one quote each, and one SENT order per supplier
    lines = [(item, "5")]
    rfq = rfq_sent(w, lines, [sup_a, sup_b])
    buyer = w.api("comprador")
    for s, price in ((sup_a, "2100"), (sup_b, "2050")):
        _ok(
            buyer.post(
                f"{PUR}/rfqs/{rfq['id']}/quotes",
                {"party": {"id": str(s.id)}, "currency": "COP", "lead_time_days": 3, "lines": quote_lines(lines, [price])},
            ),
            201,
        )
    rfq_b_only = rfq_sent(w, [(item2, "3")], [sup_b])  # invitation of supplier B only (portal BOLA target)
    orders = {}
    for key, s in (("A", sup_a), ("B", sup_b)):
        draft = _ok(
            buyer.post(
                f"{PUR}/orders",
                {
                    "party": {"id": str(s.id)},
                    "currency": "COP",
                    "notes": f"NOTA-INTERNA-{key}: no visible al proveedor",
                    "lines": [
                        {
                            "line_no": 1,
                            "item_id": str(item2.id),
                            "description": item2.name,
                            "quantity": {"value": "10", "uom": "UND"},
                            "unit_price": {"amount": "1600", "currency": "COP"},
                        }
                    ],
                },
            ),
            201,
        )
        orders[key] = order_sent(w, draft["id"])
    receive_order(w, orders["A"], wh, "4")

    # treasury: a posted recibo de caja (capital) — gives receipts, funds, concepts, journal entries
    capital_in(w, "5000000")

    # sales: customer, price, confirmed order → delivery → invoice (AR open item)
    party = customer(w, "CLI-IDOR")
    base_price(w, item, "4000")
    sales = w.api("vendedor")
    line = {"item_id": str(item.id), "quantity": "2", "tax_code": "IVA19", "warehouse_id": str(wh.id)}
    order = _ok(sales.post(f"{SALES}/orders", {"party": str(party.id), "document_date": TODAY.isoformat(), "lines": [line]}), 201)
    _ok(sales.post(f"{SALES}/orders/{order['id']}:confirm"))
    delivery = _ok(sales.post(f"{SALES}/deliveries", {"order": order["id"], "warehouse_id": str(wh.id)}), 201)
    for action in ("pick", "ship"):
        r = sales.post(f"{SALES}/deliveries/{delivery['id']}:{action}")
        if r.status_code != 200:
            break
    return {"suppliers": {"A": sup_a, "B": sup_b}, "customer": party, "rfq": rfq, "rfq_b_only": rfq_b_only, "orders": orders, "item": item}


def _all_powerful(world: Any, user: str, companies: list[str]) -> None:
    if "idor_all" not in world.roles:
        world.add_role("idor_all", ALL_GRANTS, max_cls="restricted-pii")
    world.add_user(user, companies, subject=str(uuid.uuid4()))
    for c in companies:
        world.assign(user, "idor_all", c)


@contextmanager
def idor_world() -> Iterator[IdorWorld]:
    from g2_world import gate_world
    from iam.tests.portal_support import onboard
    from iam.testing.world import make_world
    from kernel import services
    from kernel.interfaces.notifications import NotificationService
    from purchasing.requisitions.tests.kit import Api, onboard_supplier

    with gate_world() as w:
        world = w.world
        seeded = _seed_t1(w)
        _all_powerful(world, "idor_admin", ["ALFA", "BETA"])
        _all_powerful(world, "idor_beta", ["BETA"])
        world.add_role("idor_me", ["iam.me.read"])
        world.add_user("idor_nogrant", ["ALFA"], subject=str(uuid.uuid4()))
        world.assign("idor_nogrant", "idor_me", "ALFA")

        t2 = make_world((("GAMMA", "900000011"), ("DELTA", "900000012")))
        _all_powerful(t2, "t2_admin", ["GAMMA"])

        iw = IdorWorld(w, t2.tenant_id, dict(t2.companies))
        iss = w.issuer
        for key, user, tenant, company in (
            ("admin", "idor_admin", world, world.companies["ALFA"]),
            ("beta", "idor_beta", world, world.companies["BETA"]),
            ("nogrant", "idor_nogrant", world, world.companies["ALFA"]),
            ("t2admin", "t2_admin", t2, t2.companies["GAMMA"]),
        ):
            iw.tokens[key] = str(iss.issue(str(tenant.users[user].idp_subject), tenant.tenant_id, "erp-web"))
            iw.companies[key] = company

        # rows created by the all-powerful admin itself (formulas have no kit helper)
        admin_api = Api(iw.tokens["admin"], world.companies["ALFA"])
        _ok(
            admin_api.post(
                "/api/v1/workflow/formulas",
                {
                    "code": "comision_idor",
                    "domain": "pricing",
                    "expression": "round(venta * pct / 100, 2)",
                    "inputs": {"venta": "money", "pct": "percent"},
                    "result_type": "money",
                    "tests": {"cases": [{"inputs": {"venta": "1000", "pct": "5"}, "expected": "50"}]},
                },
            ),
            201,
        )
        notifications = services.get(NotificationService)
        sup = seeded["suppliers"]
        iw.portal["supA"] = onboard_supplier(w, notifications, sup["A"], "idor-prov-a")
        iw.portal["supB"] = onboard_supplier(w, notifications, sup["B"], "idor-prov-b")
        iw.party_ids["supA"], iw.party_ids["supB"] = str(sup["A"].party_id), str(sup["B"].party_id)
        cus_party = seeded["customer"].id
        iw.portal["cus"], _ = onboard(world, iss, admin_api, notifications, "customer", cus_party, "idor-cli")
        iw.party_ids["cus"] = str(cus_party)
        emp_party = uuid.uuid4()
        try:
            iw.portal["emp"], _ = onboard(world, iss, admin_api, notifications, "employee", emp_party, "idor-emp")
            iw.party_ids["emp"] = str(emp_party)
        except AssertionError:
            pass  # employee invitations need a real HR employee; recorded by the suite as not onboarded
        iw.seeded = seeded  # type: ignore[attr-defined]
        yield iw


def today() -> dt.date:
    from purchasing.requisitions.tests.kit import TODAY

    return TODAY
