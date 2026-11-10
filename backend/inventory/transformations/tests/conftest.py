"""Fixtures for the P09c tests (recipes + transformations): the P09a inventory world (real PostgreSQL with forced
RLS, real IAM policy, real ledger posting) plus the synthetic ledger roles the transformation engine posts to.

Account codes and names are SINTÉTICOS (no real PUC claim)."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pytest

from inventory.bom import service as boms
from inventory.models import Item, Warehouse
from inventory.tests.conftest import *  # noqa: F403 - re-export the inventory fixtures (inv, world, login, clerk …)
from inventory.tests.conftest import INV_GRANTS, TODAY, Inv, _issuer_session  # noqa: F401 - session fixture
from inventory.transformations import service as xf
from inventory.transformations.registry import register_fields
from kernel.uow import run_in_unit_of_work
from ledger.models import Account, AccountDetermination

D = Decimal
EXTRA_ACCOUNTS = (
    ("529505", "Mermas y desperdicios (SINTÉTICO)", "expense", "debit", "SCRAP_EXPENSE"),
    ("239595", "Costos adicionales por distribuir (SINTÉTICO)", "liability", "credit", "TRANSFORMATION_COST_CLEARING"),
    ("429505", "Aprovechamientos inventario (SINTÉTICO)", "income", "credit", "INVENTORY_ADJ_GAIN"),
    ("529510", "Pérdida en inventario (SINTÉTICO)", "expense", "debit", "INVENTORY_ADJ_LOSS"),
    ("143010", "Productos en proceso cárnicos (SINTÉTICO)", "asset", "debit", "INVENTORY_CARNICOS"),
)


@pytest.fixture(autouse=True)
def _p09c_fields() -> Iterator[None]:
    register_fields()
    yield


@dataclass
class Xf:
    """Helpers over :class:`Inv` for BOM and transformation documents."""

    inv: Inv
    wh: Warehouse

    def run(self, fn: Any, principal: uuid.UUID | None = None) -> Any:
        ctx = self.inv.world.ctx(["ALFA"], principal=principal) if principal else self.inv.ctx()
        return run_in_unit_of_work(fn, ctx)

    def bin(self, code: str = "ALMACEN") -> Any:
        return self.inv.bin(self.wh, code)

    def stock(self, item: Item, qty: str, cost: str, **kw: Any) -> Any:
        return self.inv.receive(
            self.inv.line(item, self.wh, qty, cost=cost, location=kw.pop("location", self.bin()), **kw)
        )

    # ------------------------------------------------------------------ BOM
    def bom(self, code: str, output: Item, out_qty: str, lines: list[tuple[Item, str, str]], **kw: Any) -> Any:
        byproducts = kw.pop("byproducts", [])
        data = boms.BomInput(
            code=code,
            name=f"Receta {code} (SINTÉTICA)",
            item_id=output.id,
            valid_from=kw.pop("valid_from", dt.date(2026, 1, 1)),
            valid_to=kw.pop("valid_to", None),
            output_qty=D(out_qty),
            yield_percent=D(kw.pop("yield_percent", "100")),
            lines=(
                *(boms.LineInput(item_id=i.id, quantity=D(q), scrap_percent=D(s)) for i, q, s in lines),
                *(
                    boms.LineInput(item_id=i.id, quantity=D(q), is_byproduct=True, cost_share_percent=D(sh))
                    for i, q, sh in byproducts
                ),
            ),
            **kw,
        )
        return self.run(lambda u: boms.create(u, self.inv.company(), data))

    def activate(self, bom: Any, supersede: bool = False) -> Any:
        return self.run(lambda u: boms.activate(u, bom, supersede=supersede))

    # ------------------------------------------------------------------ transformations
    def order(self, kind: str, lines: list[xf.LineInput], **kw: Any) -> Any:
        data = xf.OrderInput(
            transformation_type=kind, warehouse_id=self.wh.id, document_date=TODAY, lines=tuple(lines), **kw
        )
        return self.run(lambda u: xf.create(u, self.inv.company(), data))

    def post(self, o: Any, principal: uuid.UUID | None = None) -> Any:
        return self.run(lambda u: xf.post(u, o), principal)


def add_accounts(inv: Inv, company: str = "ALFA") -> None:
    ctx = inv.ctx(company)
    cid = inv.company(company)

    def fn(u: Any) -> None:
        for code, name, typ, nature, role in EXTRA_ACCOUNTS:
            acc = Account.objects.create(
                tenant_id=ctx.tenant_id, company_id=cid, code=code, name=name, type=typ, nature=nature
            )
            AccountDetermination.objects.create(tenant_id=ctx.tenant_id, company_id=cid, role_code=role, account=acc)

    run_in_unit_of_work(fn, ctx)


@pytest.fixture
def xw(inv: Inv) -> Xf:
    add_accounts(inv)
    return Xf(inv, inv.warehouse("WT"))


@pytest.fixture
def approver(world: Any, inv: Inv) -> str:
    """Second inventory user (``jefe``) so approvals respect segregation of duties (D-023)."""
    if "inv_admin" not in world.roles:
        world.add_role("inv_admin", INV_GRANTS, max_cls="confidential")
    world.add_user("jefe", ["ALFA"], subject=str(uuid.uuid4()))
    world.assign("jefe", "inv_admin", "ALFA")
    return "jefe"
