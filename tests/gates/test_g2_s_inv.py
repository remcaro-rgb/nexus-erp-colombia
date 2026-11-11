"""G2 gate test T-X-QA-02 (gate-owned, written by the wave-05 integrator; synthetic data, DEV only).

S-INV on the integrated product: P11 purchase order in kilograms → receipt **by presentation** (box of 20 kg) into a
leaf bin (P09 putaway target) → P09c portioning of the carcass into main/secondary cuts with normal and abnormal
waste → P09 stock issue of a cut to a cost center (concept CONSUMO, dimension on the expense line) → the kardex
(stock moves / valuation) and the GL agree, per item and in total, and the trial balance is balanced.

World: ``g2_world`` (real policy engine/OIDC, P05 ledger, P09 inventory, P09c transformations, P11 purchasing).
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from django.db.models import Sum
from g2_world import PUR, gate_world, gl, money

from purchasing.requisitions.tests.kit import TODAY, P11bWorld, order_sent

D = Decimal
INV = "/api/v1/inventory"
pytestmark = pytest.mark.django_db

#: synthetic accounts of the transformation / issue roles (SINTÉTICO, not a legal chart of accounts)
INV_ACCOUNTS = (
    ("529505", "Mermas y desperdicios (SINTÉTICO)", "expense", "debit", "SCRAP_EXPENSE"),
    ("239595", "Costos adicionales por distribuir (SINTÉTICO)", "liability", "credit", "TRANSFORMATION_COST_CLEARING"),
    ("429505", "Aprovechamientos inventario (SINTÉTICO)", "income", "credit", "INVENTORY_ADJ_GAIN"),
    ("529510", "Pérdida en inventario (SINTÉTICO)", "expense", "debit", "INVENTORY_ADJ_LOSS"),
    ("519596", "Consumo interno (SINTÉTICO)", "expense", "debit", "INVENTORY_CONSUMPTION_EXPENSE"),
    ("729595", "Consumo directo a proyectos (SINTÉTICO)", "expense", "debit", "INVENTORY_DIRECT_CONSUMPTION"),
    ("539595", "Bajas de inventario (SINTÉTICO)", "expense", "debit", "INVENTORY_SCRAP_EXPENSE"),
    ("523595", "Muestras comerciales (SINTÉTICO)", "expense", "debit", "INVENTORY_SAMPLES_EXPENSE"),
    ("529595", "Otras salidas de inventario (SINTÉTICO)", "expense", "debit", "INVENTORY_OTHER_ISSUE_EXPENSE"),
)


@pytest.fixture
def w(db: Any) -> Iterator[P11bWorld]:
    from inventory.issues import registry as issue_registry
    from inventory.transformations.registry import register_fields
    from kernel import services
    from kernel.interfaces.documents import DocumentRenderer
    from kernel.testing.fakes import FakeDocumentRenderer

    renderer: DocumentRenderer = FakeDocumentRenderer()
    with gate_world() as world, services.override(DocumentRenderer, renderer):
        register_fields()
        issue_registry.register_fields()
        yield world


def boot_inventory(w: P11bWorld) -> dict[str, Any]:
    from inventory import masters
    from inventory.issues import service as issues
    from inventory.locations.service import LocationInput, create_location
    from inventory.models import Item, Warehouse
    from ledger.dimensions import service as dims
    from ledger.models import Account, AccountDetermination

    cid, tenant = w.company(), w.world.tenant_id

    def accounts(u: Any) -> None:
        for code, name, typ, nature, role in INV_ACCOUNTS:
            acc = Account.objects.create(tenant_id=tenant, company_id=cid, code=code, name=name, type=typ,
                                         nature=nature)  # fmt: skip
            AccountDetermination.objects.create(tenant_id=tenant, company_id=cid, role_code=role, account=acc)
        issues.seed_concepts(u, cid)
        masters.create_uom(u, cid, {"code": "KG", "name": "Kilogramo", "precision": 3})

    w.run(accounts, user="bodega")
    wh = w.warehouse("PLANTA")
    with w.scope():
        wh = Warehouse.objects.get(id=wh.id)
    cold = LocationInput(code="FRIO-01", name="Cuarto frío 01", location_type="INTERNAL", parent_id=wh.root_location_id)
    bin_ = w.run(lambda u: create_location(u, wh, cold), user="bodega")
    canal = w.item("RES-CANAL", uom="KG")
    lomo = w.run(lambda u: masters.create_item(u, cid, {"code": "LOMO", "name": "Lomo (SINTÉTICO)", "base_uom": "KG",
                                                        "standard_cost": "40000"}), user="bodega")  # fmt: skip
    molida = w.run(lambda u: masters.create_item(u, cid, {"code": "MOLIDA", "name": "Molida (SINTÉTICO)",
                                                          "base_uom": "KG", "standard_cost": "18000"}),
                   user="bodega")  # fmt: skip
    with w.scope():
        item = Item.objects.select_related("base_uom").get(id=canal.id)
    caja = w.run(lambda u: masters.create_presentation(u, item, {"code": "CAJA20", "name": "Caja x20 kg",
                                                                 "uom": "caja20", "factor": "20"}),
                 user="bodega")  # fmt: skip
    node = w.run(lambda u: dims.create_node(u, cid, "cost_center", dims.NodeInput(
        code="CC-COCINA", description="Cocina central (SINTÉTICO)", node_type="DETALLE", valid_from="2026-01-01")),
        user="tesorero")  # fmt: skip
    concept = next(c for c in w.run(lambda u: issues.seed_concepts(u, cid), user="bodega") if c.code == "CONSUMO")
    return {"wh": wh, "bin": bin_, "canal": canal, "lomo": lomo, "molida": molida, "caja": caja,
            "cc": node, "concept": concept}  # fmt: skip


def kardex(w: P11bWorld, item: Any = None) -> tuple[Decimal, Decimal]:
    """(Σ qty in − out, Σ value) of the DONE stock moves, optionally of one item."""
    from inventory.models import ItemValuation, StockMove

    with w.scope():
        moves = StockMove.objects.filter(company_id=w.company(), state="done")
        vals = ItemValuation.objects.filter(company_id=w.company())
        if item is not None:
            moves, vals = moves.filter(item_id=item.id), vals.filter(item_id=item.id)
        value = moves.aggregate(v=Sum("value"))["v"] or D(0)
        valuation = vals.aggregate(q=Sum("qty"), v=Sum("value"))
    assert value == (valuation["v"] or D(0))  # kardex (moves) = valuation (P09 sub-ledger)
    return valuation["q"] or D(0), value


def test_T_X_QA_02_s_inv_presentation_receipt_portioning_issue_kardex_equals_gl(w: P11bWorld) -> None:
    from inventory.models import PackagedQuant, StockQuant
    from inventory.transformations import service as xf
    from ledger.models import JournalLine

    s = boot_inventory(w)
    wh, bin_, canal, lomo, molida = s["wh"], s["bin"], s["canal"], s["lomo"], s["molida"]

    # 1. PO 40 kg of carcass @ 15,000/kg → receipt of 2 boxes x 20 kg into the cold-room bin
    vendor = w.supplier("PROV-CARNE")
    buyer = w.api("comprador")
    r = buyer.post(f"{PUR}/orders", {
        "document_date": TODAY.isoformat(), "party": {"id": str(vendor.id)}, "currency": "COP",
        "lines": [{"line_no": 1, "item_id": str(canal.id), "description": "Canal de res (SINTÉTICO)",
                   "quantity": {"value": "40", "uom": "KG"}, "unit_price": money("15000")}],
    })  # fmt: skip
    assert r.status_code == 201, r.content
    po = order_sent(w, r.json()["id"])
    store = w.api("bodega")
    r = store.post(f"{PUR}/receipts", {
        "order": {"id": po["id"]}, "warehouse_id": str(wh.id),
        "lines": [{"order_line_id": po["lines"][0]["id"], "quantity": {"value": "40", "uom": "KG"},
                   "presentation_id": str(s["caja"].id), "location_id": str(bin_.id)}],
    })  # fmt: skip
    assert r.status_code == 201, r.content
    v = store.post(f"{PUR}/receipts/{r.json()['id']}:validate")
    assert v.status_code == 200, v.content
    with w.scope():
        quant = StockQuant.objects.get(item_id=canal.id, location_id=bin_.id)
        packs = PackagedQuant.objects.filter(presentation_id=s["caja"].id)
        assert quant.on_hand == D(40)
        assert sum((p.qty_packages for p in packs), D(0)) == D(2)  # 2 boxes x 20 kg in that bin
    assert kardex(w, canal) == (D(40), D("600000.0000"))
    b = gl(w)
    assert b["143505"] == D("600000.0000") and b["229505"] == D("-600000.0000")

    # 2. portioning of 40 kg: lomo 14 kg (main), molida 24 kg, normal waste 1.5 kg, abnormal waste 0.5 kg
    L = xf.LineInput
    data = xf.OrderInput(transformation_type="PORTIONING", warehouse_id=wh.id, document_date=TODAY, lines=(
        L("INPUT", canal.id, D("40"), location_id=bin_.id),
        L("OUTPUT", lomo.id, D("14"), actual_weight=D("14"), is_main=True, location_id=bin_.id),
        L("OUTPUT", molida.id, D("24"), actual_weight=D("24"), location_id=bin_.id),
        L("WASTE", canal.id, D("1.5")),
        L("WASTE", canal.id, D("0.5"), abnormal=True),
    ))  # fmt: skip
    order = w.run(lambda u: xf.create(u, w.company(), data), user="bodega")
    order = w.run(lambda u: xf.post(u, order), user="bodega")
    assert order.status == "POSTED"
    waste = D("7500.0000")  # 0.5 kg x 15,000 abnormal waste, expensed
    assert order.waste_expensed == waste and order.value_out + order.waste_expensed == order.value_in
    assert kardex(w, canal) == (D(0), D(0))
    q_lomo, v_lomo = kardex(w, lomo)
    q_mol, v_mol = kardex(w, molida)
    assert (q_lomo, q_mol) == (D(14), D(24)) and v_lomo + v_mol == D("592500.0000")
    b = gl(w)
    assert b["529505"] == waste and b["143505"] == D("592500.0000")

    # 3. issue 5 kg of molida to the kitchen cost center (concept CONSUMO) through the API
    r = store.post(f"{INV}/issues", {
        "concept_id": str(s["concept"].id), "warehouse_id": str(wh.id),
        "dimensions": {"cost_center_id": str(s["cc"].id)},
        "lines": [{"item_id": str(molida.id), "quantity": {"value": "5", "uom": "KG"}, "location_id": str(bin_.id)}],
    })  # fmt: skip
    assert r.status_code == 201, r.content
    posted = store.post(f"{INV}/issues/{r.json()['id']}:post")
    assert posted.status_code == 200, posted.content
    issued = D(posted.json()["total"]["amount"])
    assert issued == (v_mol / D(24) * D(5)).quantize(D("0.0001"))
    with w.scope():
        exp = list(JournalLine.objects.filter(entry_id=posted.json()["journal_entry_id"], account__code="519596"))
    assert [(x.debit, x.cost_center_id) for x in exp] == [(issued, s["cc"].id)]

    # 4. kardex and GL agree: per item, in total, and the TB is balanced
    q_mol, v_mol2 = kardex(w, molida)
    assert q_mol == D(19) and v_mol2 == v_mol - issued
    _q, total = kardex(w)
    b = gl(w)
    assert total == b["143505"] == D("592500.0000") - issued
    assert b["519596"] == issued and b["529505"] == waste
    assert b["229505"] == D("-600000.0000")
    assert sum(b.values()) == D(0)
