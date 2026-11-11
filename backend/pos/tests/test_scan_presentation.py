"""T-INV-BARCODE-05 / T-INV-PRES-04 — POS scanning by presentation barcode (REAL P09 stock + REAL P05 ledger).

Opening stock of GAS-350 in the POS warehouse: 2 boxes "Caja x12" (24 units, packaged) + 6 loose units = 30 units,
average cost 1,000. Base price 2,500 per unit (synthetic), IVA19 synthetic 19 %.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from pos.models import TicketLine
from pos.services import checkout
from pos.testing.support import PosEnv, open_counter
from pos.tests.helpers import checkout_params, gl, stock
from sales.testing.support import ApiClient

pytestmark = pytest.mark.django_db


def test_T_INV_BARCODE_05_scan_input_adds_the_right_presentation_line(pos_env: PosEnv, api: ApiClient) -> None:
    env = pos_env
    session = open_counter(env)
    api.as_("cashier")
    box, six, soda = env.presentations["box"], env.presentations["six"], env.items["soda"]
    lines = [
        {"barcode": "2001234567893\r\n"},  # keyboard-wedge scanner: value + CR/LF → the Caja x12 presentation
        {"barcode": " 2009876543213", "quantity": "2"},  # six pack
        {"barcode": "7701234567897"},  # item GTIN → loose unit (no presentation)
        {"barcode": "07701234567897"},  # GTIN-14 spelling of the same item
    ]
    r = api.post("tickets:preview", {"parameters": {"session_id": str(session.id), "lines": lines}})
    assert r.status_code == 200, r.content
    got = r.json()["data"]["lines"]
    assert [(x["presentation_id"], x["factor"], x["base_qty"], x["quantity"]) for x in got] == [
        (str(box.id), "12.000000", "12.000000", "1"),
        (str(six.id), "6.000000", "12.000000", "2"),
        (None, "1.000000", "1.000000", "1"),
        (None, "1.000000", "1.000000", "1"),
    ]
    assert all(x["item_id"] == str(soda.id) for x in got)
    assert [x["unit_price"] for x in got] == ["30000.00", "15000.00", "2500.00", "2500.00"]  # base price × factor
    assert got[0]["description"] == "Gaseosa 350 ml (SINTÉTICO) — Caja x12"

    # unknown barcode → barcode_unknown (404); scans never post anything
    r = api.post("tickets:preview", {"parameters": {"session_id": str(session.id), "lines": [{"barcode": "999"}]}})
    assert r.status_code == 404 and r.json()["code"] == "barcode_unknown", r.content

    # the same scan through checkout stores the presentation on the ticket line
    params = checkout_params(session, [{"barcode": "2001234567893"}], [{"type": "CASH", "amount": "35700"}])
    r = api.post("tickets:checkout", {"parameters": params}, key=params["client_uuid"])
    assert r.status_code == 200, r.content
    line = r.json()["data"]["lines"][0]
    assert line["presentation_id"] == str(box.id) and line["barcode"] == "2001234567893"
    assert line["quantity"] == {"value": "1.000000", "uom": "PKG"} and line["base_qty"] == "12.000000"
    assert "cost_value" not in line and "unit_cost" not in line  # cashier: cost fields are confidential


def test_T_INV_PRES_04_scan_sells_the_package_and_decrements_packaged_and_canonical_stock(pos_env: PosEnv) -> None:
    env = pos_env
    session = open_counter(env)
    soda, box = env.items["soda"], env.presentations["box"]
    assert stock(env, soda, box) == (Decimal(30), Decimal(2))
    params = checkout_params(
        session, [{"barcode": "2001234567893", "quantity": "1"}], [{"type": "CASH", "tendered": "40000"}]
    )
    ticket = env.run("cashier", lambda u: checkout.checkout(u, env.company, params))
    assert ticket.status == "POSTED" and ticket.number == "SETP1"
    assert ticket.total == Decimal("35700.0000") and ticket.change_due == Decimal("4300.0000")
    # one package sold: packaged 2 → 1 and canonical 30 → 18 (never counted twice)
    assert stock(env, soda, box) == (Decimal(18), Decimal(1))
    from kernel.testing.tenancy import bound

    with bound(env.world.ctx("admin")):
        line = TicketLine.objects.get(ticket=ticket)
        assert (line.presentation_id, line.quantity, line.base_qty) == (box.id, Decimal(1), Decimal(12))
        assert line.cost_value == Decimal("12000.0000")  # 12 base units at the average cost 1,000
    books = gl(env, ticket.id)
    assert books["110510"] == Decimal("35700.00")  # Dr CASH_POS (cash net of change)
    assert books["413595"] == Decimal("-30000.00") and books["240805"] == Decimal("-5700.00")
    assert books["613595"] == Decimal("12000.00") and books["143505"] == Decimal("-12000.00")
    assert sum(books.values()) == 0

    # selling more boxes than are packaged fails as a whole (whole packages only; nothing posted)
    many = checkout_params(session, [{"barcode": "2001234567893", "quantity": "2"}], [{"type": "CASH", "amount": "71400"}])
    with pytest.raises(Exception) as exc:
        env.run("cashier", lambda u: checkout.checkout(u, env.company, many))
    assert getattr(exc.value, "code", "") in ("stock_insufficient", "insufficient_stock")
    assert stock(env, soda, box) == (Decimal(18), Decimal(1))
