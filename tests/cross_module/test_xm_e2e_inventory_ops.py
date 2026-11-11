"""P22c — inventory operations feature e2e (gate G3): T-INV-ISSUE-05 (salidas por concepto) and T-INV-LOC-05
(ubicaciones, recepción a ubicación, movimiento entre ubicaciones, existencias por ubicación).

Runner: ``qa_harness.e2e`` (real Django on loopback over a real PostgreSQL world, real P08 shell + P09 pages in system
Chrome). Specs: ``tests/e2e/features/T-INV-ISSUE-05.issues.spec.ts`` and
``tests/e2e/features/T-INV-LOC-05.locations.spec.ts``.

World: ``g2_world.gate_world`` (P11b kit: real P09 inventory, P05 ledger + dimensions, P11a purchasing, P04 policy)
plus, for the issues, the P17 ``DocumentRenderer`` contract double (P17 is not delivered; the real renderer is proven
at G3 by P17) — the same double the P09b module tests install. All data is synthetic (SINTÉTICO); no legal values.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from qa_harness import e2e

pytestmark = pytest.mark.django_db(transaction=True)

#: synthetic expense accounts for the seeded issue concepts (code, name, account-determination role)
ISSUE_ACCOUNTS = (
    ("519590", "Consumo interno (SINTÉTICO)", "INVENTORY_CONSUMPTION_EXPENSE"),
    ("729595", "Consumo directo a proyectos (SINTÉTICO)", "INVENTORY_DIRECT_CONSUMPTION"),
    ("539595", "Bajas de inventario (SINTÉTICO)", "INVENTORY_SCRAP_EXPENSE"),
    ("523595", "Muestras comerciales (SINTÉTICO)", "INVENTORY_SAMPLES_EXPENSE"),
    ("529595", "Otras salidas de inventario (SINTÉTICO)", "INVENTORY_OTHER_ISSUE_EXPENSE"),
)


@contextmanager
def issue_world() -> Iterator[Any]:
    """gate_world + the P17 DocumentRenderer contract double (deterministic pseudo-PDF, never a real document)."""
    from g2_world import gate_world

    from kernel import services
    from kernel.interfaces.documents import DocumentRenderer
    from kernel.testing.fakes import FakeDocumentRenderer

    renderer: DocumentRenderer = FakeDocumentRenderer()
    with gate_world() as w, services.override(DocumentRenderer, renderer):
        yield w


def world_inv_issue(w: Any) -> dict[str, Any]:
    """Seeded concepts (all five set to require approval so every issue goes create → submit → approve → post),
    expense accounts, a cost center and a project (ítem contable) node, 50 UND of TORNILLO-SYN at 1.000 COP in
    PRINCIPAL; ``bodega`` requests/posts, ``supervisor`` approves (D-023)."""
    from inventory.issues import service as issues
    from ledger.dimensions import service as dims
    from ledger.models import Account, AccountDetermination

    world = w.world
    cid = w.company()

    def accounts(u: Any) -> None:
        for code, name, role in ISSUE_ACCOUNTS:
            acc = Account.objects.create(
                tenant_id=world.tenant_id, company_id=cid, code=code, name=name, type="expense", nature="debit"
            )
            AccountDetermination.objects.create(tenant_id=world.tenant_id, company_id=cid, role_code=role, account=acc)

    w.run(accounts)

    def seed(u: Any) -> list[Any]:
        out = []
        for c in issues.seed_concepts(u, cid):
            out.append(issues.update_concept(u, c, {"requires_approval": True}))
        return out

    concepts = w.run(seed)

    def node(dimension: str, code: str) -> str:
        data = dims.NodeInput(
            code=code, description=f"Nodo {code} (SINTÉTICO)", node_type="DETALLE", valid_from="2026-01-01"
        )
        return str(w.run(lambda u: dims.create_node(u, cid, dimension, data)).id)

    cost_center = node("cost_center", "CC-PROD")
    project = node("item_contable", "OBRA-01")
    item = w.item("TORNILLO-SYN")
    wh = w.warehouse("PRINCIPAL")
    w.receive(item, wh, "50", cost="1000")

    world.add_role("inv_supervisor", ["inventory.*.*", "workflow.approval.approve"], max_cls="confidential")
    world.add_user("supervisor", ["ALFA"], subject=str(uuid.uuid4()))
    world.assign("supervisor", "inv_supervisor", "ALFA")
    return {
        "user": "bodega",
        "tokens": e2e.tokens(w, "bodega", "supervisor"),
        "data": {
            "company_id": str(cid),
            "item_id": str(item.id),
            "item_code": item.code,
            "warehouse_id": str(wh.id),
            "warehouse_label": f"{wh.code} — {wh.name}",
            "cost_center_id": cost_center,
            "project_id": project,
            "concepts": {c.code: str(c.id) for c in concepts},
            "accounts": {role: code for code, _n, role in ISSUE_ACCOUNTS},
        },
    }


def world_inv_loc(w: Any) -> dict[str, Any]:
    """Warehouse CENTRAL (default bin tree, D-014), item PERNO-SYN and a SENT purchase order of 10 UND to receive."""
    from purchasing.requisitions.tests.kit import PUR, order_sent

    item = w.item("PERNO-SYN")
    wh = w.warehouse("CENTRAL")
    supplier = w.supplier("PROV-FERRETERIA")
    r = w.api("comprador").post(
        f"{PUR}/orders",
        {
            "party": {"id": str(supplier.id)},
            "currency": "COP",
            "lines": [
                {
                    "line_no": 1,
                    "item_id": str(item.id),
                    "description": item.name,
                    "quantity": {"value": "10", "uom": "UND"},
                    "unit_price": {"amount": "1500", "currency": "COP"},
                }
            ],
        },
    )
    assert r.status_code == 201, r.content  # noqa: S101 - world setup precondition
    order = order_sent(w, r.json()["id"])
    return {
        "user": "bodega",
        "tokens": e2e.tokens(w, "bodega"),
        "data": {
            "company_id": str(w.company()),
            "item_id": str(item.id),
            "item_label": f"{item.code} — {item.name}",
            "warehouse_id": str(wh.id),
            "warehouse_code": wh.code,
            "warehouse_label": f"{wh.code} — {wh.name}",
            "root_location_id": str(wh.root_location_id),
            "order_id": order["id"],
            "order_number": order["number"],
            "order_line_id": order["lines"][0]["id"],
        },
    }


def test_T_INV_ISSUE_05_ui_create_approve_post_print_reverse_issue_per_concept() -> None:
    e2e.run_feature("T-INV-ISSUE-05", world_inv_issue, augment=True, known_gaps=("DEF-P22-030",), world=issue_world)


def test_T_INV_LOC_05_ui_create_bins_receive_move_and_stock_per_bin() -> None:
    e2e.run_feature("T-INV-LOC-05", world_inv_loc, known_gaps=("DEF-P22-033", "DEF-P22-034"))
