"""F-INV-ISSUE — stock issues by concept: posting per seeded concept, dimension/reason/approval rules, reversal.

Real PostgreSQL (RLS), real IAM policy engine, real ledger PostingService, real StockService (P09a)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from inventory import masters
from inventory.issues import service as issues
from inventory.issues.models import StockIssue
from inventory.models import ItemValuation, StockMove, StockQuant, ValuationLayer
from kernel.models import OutboxMessage
from ledger.models import JournalEntry, JournalLine

pytestmark = pytest.mark.django_db
D = Decimal
BASE = "/api/v1/inventory"


def _entry_lines(inv: Any, entry_id: Any) -> list[tuple[str, Decimal, Decimal, Any, Any]]:
    with inv.scope():
        rows = JournalLine.objects.filter(entry_id=entry_id, entry__book__code="NIIF").select_related("account")
        return sorted((r.account.code, r.debit, r.credit, r.cost_center_id, r.item_dimension_id) for r in rows)


def _issue(api: Any, iw: Any, concept: str, wh: Any, lines: list[dict[str, Any]], **extra: Any) -> Any:
    body = {"concept_id": str(iw.concept(concept).id), "warehouse_id": str(wh.id), "lines": lines, **extra}
    return api.post(f"{BASE}/issues", body)


def test_T_INV_ISSUE_01_each_seeded_concept_posts_exact_journal_and_moves_cost_from_layers(iw, clerk, login):
    inv = iw.inv
    api = login(clerk)
    wh = inv.warehouse("BOG-SAL", branch="BOG")
    alm = inv.bin(wh, "ALMACEN")
    avg = inv.item("P-AVG")  # weighted average
    fifo = inv.item("P-FIFO", valuation_method="FIFO")
    inv.receive(
        inv.line(avg, wh, "10", "1", location=alm, cost="1000"), inv.line(fifo, wh, "5", "2", location=alm, cost="1000")
    )
    inv.receive(
        inv.line(avg, wh, "10", "1", location=alm, cost="1300"), inv.line(fifo, wh, "20", "2", location=alm, cost="1200")
    )
    # average 1150; FIFO layers 5 × 1000, 20 × 1200

    cc, project = str(iw.cost_center), str(iw.project)
    cases = [
        ("CONSUMO", {"dimensions": {"cost_center_id": cc}}, "519595"),
        ("CONSUMO_DIRECTO", {"dimensions": {"cost_center_id": cc, "item_dimension_id": project}}, "729595"),
        ("BAJA", {"reason": "VENCIDO lote de prueba (SINTÉTICO)"}, "539595"),
        ("MUESTRA", {}, "523595"),
        ("OTRO", {"reason": "Donación a fundación (SINTÉTICA)"}, "529595"),
    ]
    # each concept issues 2 × P-AVG and 3 × P-FIFO; FIFO consumption is oldest-first across the cases
    fifo_expected = [D("3000"), D("2000") + D("1200"), D("3600"), D("3600"), D("3600")]
    for n, (code, extra, account) in enumerate(cases):
        r = _issue(
            api,
            iw,
            code,
            wh,
            [
                {"item_id": str(avg.id), "quantity": "2", "location_id": str(alm.id)},
                {"item_id": str(fifo.id), "quantity": {"value": "3", "uom": "UND"}},
            ],
            **extra,
        )
        assert r.status_code == 201, (code, r.content)
        doc = r.json()
        assert doc["status"] == "DRAFT" and doc["number"].startswith("SAL-") and doc["concept_code"] == code

        sim = api.post(f"{BASE}/issues/{doc['id']}:simulate")
        assert sim.status_code == 200, (code, sim.content)
        r = api.post(f"{BASE}/issues/{doc['id']}:post")
        assert r.status_code == 200, (code, r.content)
        posted = r.json()
        avg_value, fifo_value = D("2300.0000"), fifo_expected[n]
        total = avg_value + fifo_value
        assert posted["status"] == "POSTED" and posted["total"] == {"amount": f"{total:.4f}", "currency": "COP"}
        assert [ln["amount"]["amount"] for ln in posted["lines"]] == [f"{avg_value:.4f}", f"{fifo_value:.4f}"]

        # exact journal: Dr concept account (with the line dims) / Cr inventory, per line, branch of the warehouse
        dim_cc = iw.cost_center if code in ("CONSUMO", "CONSUMO_DIRECTO") else None
        dim_pr = iw.project if code == "CONSUMO_DIRECTO" else None
        expected = sorted(
            [
                (account, avg_value, D(0), dim_cc, dim_pr),
                ("143505", D(0), avg_value, None, None),
                (account, fifo_value, D(0), dim_cc, dim_pr),
                ("143505", D(0), fifo_value, None, None),
            ]
        )
        assert _entry_lines(inv, posted["journal_entry_id"]) == expected, code
        with inv.scope():
            entry = JournalEntry.objects.get(id=posted["journal_entry_id"])
            assert entry.source_type == issues.SOURCE_TYPE and str(entry.source_id) == doc["id"]
            assert entry.branch_id == wh.branch_id
            moves = list(StockMove.objects.filter(source_id=doc["id"]).order_by("line_ref"))
            assert OutboxMessage.objects.filter(event_type="inventory.issue.posted.v1").count() == n + 1
        # stock moves: ISSUE out of the bin, cost from the valuation layers
        assert [(m.kind, m.from_location_id, m.quantity, -m.value) for m in moves] == [
            ("ISSUE", alm.id, D(2), avg_value),
            ("ISSUE", alm.id, D(3), fifo_value),
        ]
        # the simulate preview was the exact post result (and wrote nothing)
        assert sim.json()["data"]["total"] == posted["total"]
        assert sorted((j["account_code"], j["debit"], j["credit"]) for j in sim.json()["data"]["journal"]) == sorted(
            (a, f"{dr:.4f}", f"{cr:.4f}") for a, dr, cr, _c, _p in expected
        )

    with inv.scope():
        v_avg = ItemValuation.objects.get(item_id=avg.id)
        v_fifo = ItemValuation.objects.get(item_id=fifo.id)
        layers = list(ValuationLayer.objects.filter(item_id=fifo.id).order_by("created_at", "id"))
        on_hand = {q.item_id: q.on_hand for q in StockQuant.objects.filter(location_id=alm.id)}
    assert (v_avg.qty, v_avg.value) == (D(10), D("11500.0000"))
    assert (v_fifo.qty, v_fifo.value) == (D(10), D("12000.0000"))
    assert [(lyr.remaining_qty, lyr.remaining_value) for lyr in layers] == [(D(0), D(0)), (D(10), D("12000.0000"))]
    assert on_hand == {avg.id: D(10), fifo.id: D(10)}


def test_T_INV_ISSUE_02_required_dims_reason_and_approval_threshold_routes_to_workflow(
    iw, clerk, supervisor, seller, login, issue_services
):
    inv = iw.inv
    api, boss, sales = login(clerk), login(supervisor), login(seller)
    wh = inv.warehouse("BOG-DIM", branch="BOG")
    alm = inv.bin(wh, "ALMACEN")
    item = inv.item("P-DIM")
    inv.receive(inv.line(item, wh, "20", location=alm, cost="1000"))
    line = {"item_id": str(item.id), "quantity": "2", "location_id": str(alm.id)}

    # CONSUMO requires a cost center on every line: missing → dimension_required (nothing written)
    r = _issue(api, iw, "CONSUMO", wh, [line])
    assert r.status_code == 422 and r.json()["code"] == "dimension_required", r.content
    # CONSUMO_DIRECTO also requires the project (ítem contable)
    r = _issue(api, iw, "CONSUMO_DIRECTO", wh, [line], dimensions={"cost_center_id": str(iw.cost_center)})
    assert r.status_code == 422 and r.json()["code"] == "dimension_required", r.content
    with inv.scope():
        assert StockIssue.objects.count() == 0
    # a line-level dimension satisfies the rule and overrides the header
    r = _issue(
        api,
        iw,
        "CONSUMO",
        wh,
        [{**line, "dimensions": {"cost_center_id": str(iw.other_cost_center)}}],
    )
    assert r.status_code == 201, r.content
    assert r.json()["lines"][0]["dimensions"]["cost_center_id"] == str(iw.other_cost_center)
    # BAJA requires a reason with one of its reason codes; inactive concepts are refused
    r = _issue(api, iw, "BAJA", wh, [line])
    assert r.status_code == 422 and r.json()["code"] == "issue_reason_required"
    r = _issue(api, iw, "BAJA", wh, [line], reason="porque sí")
    assert r.status_code == 422 and r.json()["code"] == "issue_reason_required"
    r = api.client.delete(f"{BASE}/issue-concepts/{iw.concept('OTRO').id}", **api._h(True, {}))
    assert r.status_code == 204, r.content
    r = _issue(api, iw, "OTRO", wh, [line], reason="x")
    assert r.status_code == 422 and r.json()["code"] == "issue_concept_inactive"

    # approval threshold on MUESTRA: value ≥ 5.000 COP routes to the workflow
    muestra = iw.concept("MUESTRA")
    r = api.get(f"{BASE}/issue-concepts/{muestra.id}")
    r = api.patch(
        f"{BASE}/issue-concepts/{muestra.id}",
        {"approval_threshold_value": {"amount": "5000", "currency": "COP"}},
        HTTP_IF_MATCH=r["ETag"],
    )
    assert r.status_code == 200 and r.json()["approval_threshold_value"]["amount"] == "5000.0000", r.content

    small = _issue(api, iw, "MUESTRA", wh, [{**line, "quantity": "4"}]).json()  # 4.000 < 5.000
    r = api.post(f"{BASE}/issues/{small['id']}:submit")
    assert r.status_code == 200 and r.json()["status"] == "APPROVED" and r.json()["approval_id"] is None
    assert issue_services["workflow"].requests == {}

    big = _issue(api, iw, "MUESTRA", wh, [{**line, "quantity": "6"}]).json()  # 6.000 ≥ 5.000
    r = api.post(f"{BASE}/issues/{big['id']}:post")  # a draft that needs approval cannot be posted
    assert r.status_code == 409 and r.json()["code"] == "approval_required"
    r = api.post(f"{BASE}/issues/{big['id']}:submit")
    assert r.status_code == 200, r.content
    submitted = r.json()
    assert submitted["status"] == "SUBMITTED" and submitted["approval_id"]
    [req] = issue_services["workflow"].requests.values()
    assert req.document.type == issues.SOURCE_TYPE and str(req.document.id) == big["id"] and req.state == "PENDING"
    # stock is reserved while pending: 20 − 4 (small) − 6 = 10 available
    [a] = inv.avail(item_id=item.id, warehouse_id=wh.id)
    assert a.available == D(10)
    r = api.post(f"{BASE}/issues/{big['id']}:post")
    assert r.status_code == 409 and r.json()["code"] == "approval_required"
    r = api.post(f"{BASE}/issues/{big['id']}:approve")  # the requester never approves (D-023)
    assert r.status_code == 403 and r.json()["code"] == "sod_violation", r.content
    r = boss.post(f"{BASE}/issues/{big['id']}:approve", {"comment": "ok"})
    assert r.status_code == 200 and r.json()["status"] == "APPROVED", r.content
    r = api.post(f"{BASE}/issues/{big['id']}:post")
    assert r.status_code == 200 and r.json()["status"] == "POSTED"
    assert r.json()["total"] == {"amount": "6000.0000", "currency": "COP"}

    # sales role: reads the issue without any cost field (also not inside the lines), CSV without the value column
    r = sales.get(f"{BASE}/issues/{big['id']}")
    assert r.status_code == 200
    body = r.json()
    assert "total" not in body and all("amount" not in ln and "unit_price" not in ln for ln in body["lines"])
    csv_body = sales.get(f"{BASE}/issues:export").content.decode()
    assert "valor_cop" not in csv_body.splitlines()[0] and big["number"] in csv_body
    assert "valor_cop" in api.get(f"{BASE}/issues:export").content.decode().splitlines()[0]
    # the :simulate cost preview is confidential too
    draft = _issue(api, iw, "MUESTRA", wh, [{**line, "quantity": "1"}]).json()
    r = sales.post(f"{BASE}/issues/{draft['id']}:simulate")
    assert r.status_code == 200 and "data" not in r.json() and r.json()["status"] == "simulated", r.content
    assert api.post(f"{BASE}/issues/{draft['id']}:simulate").json()["data"]["total"]["amount"] == "1000.0000"


def test_T_INV_ISSUE_04_reversal_returns_stock_at_original_cost_bin_and_lot(iw, clerk, login):
    inv = iw.inv
    api = login(clerk)
    wh = inv.warehouse("BOG-REV", branch="BOG")
    alm, des = inv.bin(wh, "ALMACEN"), inv.bin(wh, "DESPACHO")
    item = inv.item("P-LOT", tracking="LOT")
    lots = [
        inv.run(lambda u, c=code, e=exp: masters.create_lot(u, inv.company(), item, {"code": c, "expiry_date": e}))
        for code, exp in (("L-A", "2027-01-31"), ("L-B", "2027-06-30"))
    ]
    inv.receive(inv.line(item, wh, "6", location=alm, lot=lots[0], cost="2000"))
    inv.receive(inv.line(item, wh, "6", location=des, lot=lots[1], cost="3000"))  # average now 2500

    r = _issue(
        api,
        iw,
        "BAJA",
        wh,
        [{"item_id": str(item.id), "quantity": "4", "location_id": str(des.id), "lot_id": str(lots[1].id)}],
        reason="DANADO en bodega (SINTÉTICO)",
    )
    assert r.status_code == 201, r.content
    doc = r.json()
    posted = api.post(f"{BASE}/issues/{doc['id']}:post").json()
    assert posted["status"] == "POSTED" and posted["total"]["amount"] == "10000.0000"  # 4 × average 2500
    # later receipts change the average cost; the reversal must still use the original cost
    inv.receive(inv.line(item, wh, "10", location=alm, lot=lots[0], cost="5000"))

    # a posted issue is immutable: no PATCH, no DELETE
    r = api.get(f"{BASE}/issues/{doc['id']}")
    r = api.patch(f"{BASE}/issues/{doc['id']}", {"notes": "cambio"}, HTTP_IF_MATCH=r["ETag"])
    assert r.status_code == 409 and r.json()["code"] == "invalid_state_transition"
    r = api.post(f"{BASE}/issues/{doc['id']}:reverse")
    assert r.status_code == 422 and r.json()["code"] == "reason_required"

    r = api.post(f"{BASE}/issues/{doc['id']}:reverse", {"reason": "Baja registrada por error"})
    assert r.status_code == 200, r.content
    rev = r.json()
    assert rev["status"] == "REVERSED" and rev["reversal_entry_id"] and rev["journal_entry_id"] == posted["journal_entry_id"]
    with inv.scope():
        ret = StockMove.objects.get(source_type=issues.REVERSAL_SOURCE_TYPE, source_id=doc["id"])
        orig = StockMove.objects.get(source_type=issues.SOURCE_TYPE, source_id=doc["id"])
        quant = StockQuant.objects.get(item_id=item.id, location_id=des.id, lot_id=lots[1].id)
        reversal = JournalEntry.objects.get(id=rev["reversal_entry_id"])
    # stock back in the original bin and lot, at the original cost
    assert (ret.kind, ret.to_location_id, ret.lot_id, ret.quantity) == ("RETURN", des.id, lots[1].id, D(4))
    assert ret.original_move_id == orig.id and ret.unit_cost == orig.unit_cost == D("2500.00000000")
    assert ret.value == -orig.value == D("10000.0000")
    assert quant.on_hand == D(6)
    # mirror journal entry: every account nets to zero over original + reversal
    assert str(reversal.reversal_of_id) == posted["journal_entry_id"]
    net: dict[str, Decimal] = {}
    for entry in (posted["journal_entry_id"], rev["reversal_entry_id"]):
        for code, dr, cr, _c, _p in _entry_lines(inv, entry):
            net[code] = net.get(code, D(0)) + dr - cr
    assert net and all(v == 0 for v in net.values()), net
    # valuation: 6×2000 + 6×3000 − 10000 + 10×5000 + 10000 (returned at original cost) = 80000 for 22 units
    with inv.scope():
        v = ItemValuation.objects.get(item_id=item.id)
    assert (v.qty, v.value) == (D(22), D("80000.0000"))
    r = api.post(f"{BASE}/issues/{doc['id']}:reverse", {"reason": "otra vez"})
    assert r.status_code == 409 and r.json()["code"] == "invalid_state_transition"
