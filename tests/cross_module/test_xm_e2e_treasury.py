"""P22c — treasury feature e2e (gate G3): T-TRE-RC-08 (recibos de caja) and T-TRE-CE-07 (comprobantes de egreso + lotes).

Runner: ``qa_harness.e2e`` (real Django on loopback over the G2 gate world: real P04 policy/OIDC, P05 ledger, P09,
P11 purchasing with the real AP open items, P16 treasury; P07 workflow is the kit's contract double with SoD).
Specs: ``tests/e2e/features/T-TRE-RC-08.receipts.spec.ts`` and ``tests/e2e/features/T-TRE-CE-07.disbursements.spec.ts``.

Both worlds configure an approval threshold (COP 100,000, synthetic) so the voucher really routes to approval, and add
an approver ``aprobador_tes`` (treasury approval only) different from the creator ``tesorero`` (SoD, D-023).
All amounts are synthetic.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from qa_harness import e2e

pytestmark = pytest.mark.django_db(transaction=True)

APPROVER = "aprobador_tes"
APPROVER_GRANTS = [
    "treasury.fund.read",
    "treasury.concept.read",
    "treasury.receipt.read",
    "treasury.receipt.approve",
    "treasury.disbursement.read",
    "treasury.disbursement.approve",
    "treasury.payment_batch.read",
    "treasury.payment_batch.approve",
    "workflow.approval.approve",
]


def _approver(w: Any) -> None:
    w.world.add_role("tesoreria_aprobador_e2e", APPROVER_GRANTS, max_cls="confidential")
    w.world.add_user(APPROVER, ["ALFA"], subject=str(uuid.uuid4()))
    w.world.assign(APPROVER, "tesoreria_aprobador_e2e", "ALFA")


def _threshold(w: Any, **values: str) -> None:
    from treasury.services.common import configure

    w.run(lambda u: configure(u, w.company(), **values), user="tesorero")


def world_rc(w: Any) -> dict[str, Any]:
    """Concept ``PRESTAMO-UI`` (IN, role CAPITAL → 311505), RC approval threshold COP 100,000, approver user."""
    from g2_world import fund_id

    from treasury.services.masters import create_concept

    _approver(w)
    _threshold(w, rc_approval_threshold="100000")
    concept = w.run(
        lambda u: create_concept(
            u,
            w.company(),
            {"code": "PRESTAMO-UI", "name": "Préstamo de socio (SINTÉTICO)", "direction": "IN", "account_role": "CAPITAL"},
            extended=True,
        ),
        user="tesorero",
    )
    return {
        "user": "tesorero",
        "tokens": e2e.tokens(w, "tesorero", APPROVER),
        "data": {
            "company_id": str(w.company()),
            "fund_id": fund_id(w),
            "concept_id": str(concept.id),
            "today": "2026-11-09",
        },
    }


def _vendor_bill(w: Any, vendor: Any, item: Any, wh: Any, n: int) -> Any:
    """PO → receipt → vendor invoice matched and posted (2 × 50,000 + IVA19 synthetic = 119,000) → its AP open item."""
    from g2_world import PUR, money

    from purchasing.models import ApOpenItem
    from purchasing.requisitions.tests.kit import TODAY, order_sent, receive_order

    buyer = w.api("comprador")
    r = buyer.post(
        f"{PUR}/orders",
        {
            "document_date": TODAY.isoformat(),
            "party": {"id": str(vendor.id)},
            "currency": "COP",
            "lines": [
                {
                    "line_no": 1,
                    "item_id": str(item.id),
                    "description": "Insumo (SINTÉTICO)",
                    "quantity": {"value": "2", "uom": "UND"},
                    "unit_price": money("50000"),
                    "tax_code": "IVA19",
                }
            ],
        },
    )
    assert r.status_code == 201, r.content
    po = order_sent(w, r.json()["id"])
    receive_order(w, po, wh, "2")
    r = buyer.post(
        f"{PUR}/vendor-invoices",
        {
            "document_date": TODAY.isoformat(),
            "party": po["party"],
            "vendor_invoice_number": f"FV-E2E-{n:02d}",
            "currency": "COP",
            "order": {"type": "purchasing.order", "id": po["id"]},
            "lines": [
                {
                    "order_line_id": po["lines"][0]["id"],
                    "quantity": {"value": "2", "uom": "UND"},
                    "unit_price": money("50000"),
                    "tax_code": "IVA19",
                }
            ],
        },
    )
    assert r.status_code == 201, r.content
    inv = r.json()
    r = buyer.post(f"{PUR}/vendor-invoices/{inv['id']}:match")
    assert r.status_code == 200 and r.json()["match_status"] == "MATCHED", r.content
    r = buyer.post(f"{PUR}/vendor-invoices/{inv['id']}:post")
    assert r.status_code == 200, r.content
    with w.scope():
        (ap,) = ApOpenItem.objects.filter(document_id=inv["id"])
    return ap


def world_ce(w: Any) -> dict[str, Any]:
    """Three posted vendor invoices (119,000 each, AP open items), bank funded by a capital RC, CE threshold 100,000."""
    from g2_world import capital_in, fund_id

    _approver(w)
    wh = w.warehouse("PRIN")
    item = w.item("INSUMO-TRE")
    vendors = [w.supplier(code) for code in ("PROV-CE-UI", "PROV-LOTE-1", "PROV-LOTE-2")]
    bills = [_vendor_bill(w, v, item, wh, i + 1) for i, v in enumerate(vendors)]
    capital_in(w, "1000000")
    _threshold(w, ce_approval_threshold="100000")
    return {
        "user": "tesorero",
        "tokens": e2e.tokens(w, "tesorero", APPROVER),
        "data": {
            "company_id": str(w.company()),
            "fund_id": fund_id(w),
            "today": "2026-11-09",
            "vendors": [{"party_id": str(v.party_id), "code": v.code} for v in vendors],
            "open_items": [str(b.id) for b in bills],
        },
    }


def test_T_TRE_RC_08_ui_create_approve_post_print_reverse_receipt() -> None:
    e2e.run_feature("T-TRE-RC-08", world_rc, known_gaps=("DEF-P22-020", "DEF-P22-021"))


def test_T_TRE_CE_07_ui_disbursement_lifecycle_and_batch_execution() -> None:
    e2e.run_feature("T-TRE-CE-07", world_ce, known_gaps=("DEF-P22-022", "DEF-P22-023", "DEF-P22-024"))
