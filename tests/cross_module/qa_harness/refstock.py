"""DB-backed reference implementation of ``kernel.interfaces.stock.StockService`` (P22b test double).

Why: the inventory module (P09a) is built in the same wave as P22b. The cross-module scenarios must
prove atomicity, row locking and rollback with **real PostgreSQL transactions**, which the in-memory
``kernel.testing.fakes.FakeInventory`` cannot (its state survives a rolled-back unit of work). This
double keeps the frozen contract of ``kernel/interfaces/stock.py`` and shared/07 §7:

* called inside the caller's unit of work (raises ``UnitOfWorkRequired`` otherwise);
* ``SELECT … FOR UPDATE`` on the valuation row, lines locked in sorted ``(item_id, warehouse_id)`` order;
* WAC (NUMERIC(24,8) half-even average, issue value half-even to 4 decimals, residual-on-zero) and
  FIFO (oldest layer first, last unit of a layer takes the residual);
* negative stock refused (D-032): ``409 insufficient_stock``;
* idempotent per ``(source.type, source.id, line_ref)``.

It never posts GL by itself: the reference document flows (``refdocs``) post the stock entries through
the real ``PostingService`` in the same unit of work. Scratch tables (``qa_ref_*``) are created by the
scenario fixtures inside the test database only; they never exist in a migration. Once P09 registers
``StockService`` the scenarios run against it (``drivers.real_stock()``).
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from .golden import q4, q8

DDL = """
CREATE TABLE IF NOT EXISTS qa_ref_valuation (
    tenant_id uuid NOT NULL, company_id uuid NOT NULL, item_id uuid NOT NULL, warehouse_id uuid NOT NULL,
    method text NOT NULL CHECK (method IN ('WAC', 'FIFO')),
    qty numeric(20,6) NOT NULL DEFAULT 0, value numeric(20,4) NOT NULL DEFAULT 0,
    avg_cost numeric(24,8) NOT NULL DEFAULT 0, allow_negative boolean NOT NULL DEFAULT false,
    PRIMARY KEY (tenant_id, company_id, item_id, warehouse_id),
    CHECK (allow_negative OR qty >= 0)
);
CREATE TABLE IF NOT EXISTS qa_ref_layer (
    id bigserial PRIMARY KEY, tenant_id uuid NOT NULL, company_id uuid NOT NULL, item_id uuid NOT NULL,
    warehouse_id uuid NOT NULL, source_type text NOT NULL, source_id uuid NOT NULL, line_ref text NOT NULL,
    received_qty numeric(20,6) NOT NULL, unit_cost numeric(24,8) NOT NULL,
    remaining_qty numeric(20,6) NOT NULL, remaining_value numeric(20,4) NOT NULL
);
CREATE TABLE IF NOT EXISTS qa_ref_move (
    id uuid PRIMARY KEY, tenant_id uuid NOT NULL, company_id uuid NOT NULL, item_id uuid NOT NULL,
    warehouse_id uuid NOT NULL, source_type text NOT NULL, source_id uuid NOT NULL, line_ref text NOT NULL,
    qty numeric(20,6) NOT NULL, value numeric(20,4) NOT NULL, move_date date NOT NULL,
    UNIQUE (tenant_id, source_type, source_id, line_ref)
);
"""
TABLES = ("qa_ref_valuation", "qa_ref_layer", "qa_ref_move")


def install(using: str = "default") -> None:
    from django.db import connections

    with connections[using].cursor() as cur:
        cur.execute(DDL)


def uninstall(using: str = "default") -> None:
    from django.db import connections

    with connections[using].cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS " + ", ".join(TABLES))


def _uow() -> Any:
    from kernel.uow import require_uow

    return require_uow()


def _cursor(uow: Any) -> Any:
    from django.db import connections

    return connections[uow.using].cursor()


class ReferenceStock:
    """Implements ``StockService`` (receive / issue / return_moves) plus the read helpers the
    scenarios assert on (on-hand, valuation, layers) and value-only revaluation (price difference,
    landed cost)."""

    implementation = "reference-double:P22b (P09 not merged)"

    # ----------------------------------------------------------------- master data
    def define_item(
        self,
        uow: Any,
        company_id: uuid.UUID,
        item_id: uuid.UUID,
        warehouse_id: uuid.UUID,
        method: str = "WAC",
        allow_negative: bool = False,
    ) -> None:
        uow.ctx.require_company(company_id)
        with _cursor(uow) as cur:
            cur.execute(
                "INSERT INTO qa_ref_valuation (tenant_id, company_id, item_id, warehouse_id, method, allow_negative) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                [uow.ctx.tenant_id, company_id, item_id, warehouse_id, method, allow_negative],
            )

    # ----------------------------------------------------------------- StockService
    def receive(self, uow: Any, company_id: uuid.UUID, source: Any, lines: list[Any], move_date: dt.date) -> Any:
        from kernel.errors import ValidationFailed
        from kernel.interfaces.stock import StockMoveResult

        self._require(uow, company_id)
        done = self._existing(uow, source)
        if done is not None:
            return done
        ids_out, total = [], Decimal(0)
        for line in self._sorted(lines):
            if line.quantity <= 0 or line.unit_cost is None or line.unit_cost < 0:
                raise ValidationFailed("receipt needs a positive quantity and a unit cost", reason="range")
            row = self._lock(uow, company_id, line.item_id, line.warehouse_id)
            value = q4(line.quantity * line.unit_cost)
            qty, val = row["qty"] + line.quantity, row["value"] + value
            with _cursor(uow) as cur:
                cur.execute(
                    "UPDATE qa_ref_valuation SET qty = %s, value = %s, avg_cost = %s "
                    "WHERE tenant_id = %s AND company_id = %s AND item_id = %s AND warehouse_id = %s",
                    [qty, val, q8(val / qty) if qty else Decimal(0), *self._key(uow, company_id, line)],
                )
                cur.execute(
                    "INSERT INTO qa_ref_layer (tenant_id, company_id, item_id, warehouse_id, source_type, source_id, "
                    "line_ref, received_qty, unit_cost, remaining_qty, remaining_value) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    [*self._key(uow, company_id, line), source.type, source.id, line.line_ref, line.quantity,
                     line.unit_cost, line.quantity, value],
                )  # fmt: skip
            ids_out.append(self._move(uow, company_id, source, line, line.quantity, value, move_date))
            total += value
        return StockMoveResult(tuple(ids_out), total)

    def issue(self, uow: Any, company_id: uuid.UUID, source: Any, lines: list[Any], move_date: dt.date) -> Any:
        from kernel.errors import Conflict
        from kernel.interfaces.stock import StockMoveResult

        self._require(uow, company_id)
        done = self._existing(uow, source)
        if done is not None:
            return done
        ids_out, total = [], Decimal(0)
        for line in self._sorted(lines):
            row = self._lock(uow, company_id, line.item_id, line.warehouse_id)
            if line.quantity > row["qty"] and not row["allow_negative"]:
                raise Conflict(
                    f"insufficient stock: {row['qty']} on hand, {line.quantity} requested", code="insufficient_stock"
                )
            value = self._consume(uow, company_id, line, row)
            qty = row["qty"] - line.quantity
            val = Decimal(0) if qty == 0 else row["value"] - value
            if qty == 0:
                value = row["value"]  # residual-on-zero: value = 0 exactly
            with _cursor(uow) as cur:
                cur.execute(
                    "UPDATE qa_ref_valuation SET qty = %s, value = %s "
                    "WHERE tenant_id = %s AND company_id = %s AND item_id = %s AND warehouse_id = %s",
                    [qty, val, *self._key(uow, company_id, line)],
                )
            ids_out.append(self._move(uow, company_id, source, line, -line.quantity, -value, move_date))
            total += value
        return StockMoveResult(tuple(ids_out), total)

    def return_moves(self, uow: Any, company_id: uuid.UUID, source: Any, original: Any) -> Any:
        """Customer return of ``original`` issue moves: receipt back at the **original issue cost**."""
        from kernel.errors import NotFound
        from kernel.interfaces.stock import StockLine, StockMoveResult

        self._require(uow, company_id)
        done = self._existing(uow, source)
        if done is not None:
            return done
        with _cursor(uow) as cur:
            cur.execute(
                "SELECT item_id, warehouse_id, line_ref, qty, value, move_date FROM qa_ref_move "
                "WHERE tenant_id = %s AND company_id = %s AND source_type = %s AND source_id = %s ORDER BY line_ref",
                [uow.ctx.tenant_id, company_id, original.type, original.id],
            )
            moves = cur.fetchall()
        if not moves:
            raise NotFound("original stock moves not found")
        ids_out, total = [], Decimal(0)
        for item_id, wh, ref, qty, value, day in moves:
            line = StockLine(ref, item_id, wh, -qty, unit_cost=(value / qty) if qty else Decimal(0))
            row = self._lock(uow, company_id, item_id, wh)
            new_qty, new_val = row["qty"] - qty, row["value"] - value
            with _cursor(uow) as cur:
                cur.execute(
                    "UPDATE qa_ref_valuation SET qty = %s, value = %s, avg_cost = %s "
                    "WHERE tenant_id = %s AND company_id = %s AND item_id = %s AND warehouse_id = %s",
                    [new_qty, new_val, q8(new_val / new_qty) if new_qty else Decimal(0),
                     *self._key(uow, company_id, line)],
                )  # fmt: skip
                if row["method"] == "FIFO":
                    cur.execute(
                        "INSERT INTO qa_ref_layer (tenant_id, company_id, item_id, warehouse_id, source_type, "
                        "source_id, line_ref, received_qty, unit_cost, remaining_qty, remaining_value) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        [*self._key(uow, company_id, line), source.type, source.id, ref, -qty,
                         line.unit_cost, -qty, -value],
                    )  # fmt: skip
            ids_out.append(self._move(uow, company_id, source, line, -qty, -value, day))
            total += -value
        return StockMoveResult(tuple(ids_out), total)

    # ----------------------------------------------------------------- value-only revaluation
    def revalue_receipt(
        self, uow: Any, company_id: uuid.UUID, receipt: Any, line_ref: str, delta_per_unit: Decimal
    ) -> tuple[Decimal, Decimal]:
        """Price difference / landed cost on one receipt line (shared/07 §2): the part still on hand
        revalues the stock, the consumed part is returned for COGS. Returns (inventory, cogs)."""
        with _cursor(uow) as cur:
            cur.execute(
                "SELECT id, item_id, warehouse_id, received_qty, remaining_qty FROM qa_ref_layer WHERE tenant_id = %s "
                "AND company_id = %s AND source_type = %s AND source_id = %s AND line_ref = %s FOR UPDATE",
                [uow.ctx.tenant_id, company_id, receipt.type, receipt.id, line_ref],
            )
            layer_id, item_id, wh, received, remaining = cur.fetchone()
        row = self._lock(uow, company_id, item_id, wh)
        total = q4(received * delta_per_unit)
        on_hand = min(received, row["qty"]) if row["method"] == "WAC" else remaining
        to_inventory = q4(on_hand * delta_per_unit)
        with _cursor(uow) as cur:
            new_val = row["value"] + to_inventory
            cur.execute(
                "UPDATE qa_ref_valuation SET value = %s, avg_cost = %s WHERE tenant_id = %s AND company_id = %s "
                "AND item_id = %s AND warehouse_id = %s",
                [new_val, q8(new_val / row["qty"]) if row["qty"] else Decimal(0), uow.ctx.tenant_id, company_id,
                 item_id, wh],
            )  # fmt: skip
            cur.execute(
                "UPDATE qa_ref_layer SET unit_cost = unit_cost + %s, remaining_value = remaining_value + %s "
                "WHERE id = %s",
                [delta_per_unit, q4(remaining * delta_per_unit), layer_id],
            )
        return to_inventory, total - to_inventory

    # ----------------------------------------------------------------- reads
    def state(self, ctx: Any, company_id: uuid.UUID, item_id: uuid.UUID, warehouse_id: uuid.UUID) -> dict[str, Any]:
        from django.db import connection

        with connection.cursor() as cur:
            cur.execute(
                "SELECT qty, value, avg_cost, method FROM qa_ref_valuation WHERE tenant_id = %s AND company_id = %s "
                "AND item_id = %s AND warehouse_id = %s",
                [ctx.tenant_id, company_id, item_id, warehouse_id],
            )
            qty, value, avg, method = cur.fetchone()
            cur.execute(
                "SELECT coalesce(sum(qty), 0), coalesce(sum(value), 0), count(*) FROM qa_ref_move "
                "WHERE tenant_id = %s AND company_id = %s AND item_id = %s AND warehouse_id = %s",
                [ctx.tenant_id, company_id, item_id, warehouse_id],
            )
            moves_qty, moves_value, moves = cur.fetchone()
            cur.execute(
                "SELECT coalesce(sum(remaining_qty), 0), coalesce(sum(remaining_value), 0) FROM qa_ref_layer "
                "WHERE tenant_id = %s AND company_id = %s AND item_id = %s AND warehouse_id = %s",
                [ctx.tenant_id, company_id, item_id, warehouse_id],
            )
            layer_qty, layer_value = cur.fetchone()
        return {"qty": qty, "value": value, "avg": avg, "method": method, "moves_qty": moves_qty,
                "moves_value": moves_value, "moves": moves, "layer_qty": layer_qty, "layer_value": layer_value}  # fmt: skip

    def company_value(self, ctx: Any, company_id: uuid.UUID) -> Decimal:
        from django.db import connection

        with connection.cursor() as cur:
            cur.execute(
                "SELECT coalesce(sum(value), 0) FROM qa_ref_valuation WHERE tenant_id = %s AND company_id = %s",
                [ctx.tenant_id, company_id],
            )
            return Decimal(cur.fetchone()[0])

    def move_count(self, ctx: Any, source: Any) -> int:
        from django.db import connection

        with connection.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM qa_ref_move WHERE tenant_id = %s AND source_type = %s AND source_id = %s",
                [ctx.tenant_id, source.type, source.id],
            )
            return int(cur.fetchone()[0])

    # ----------------------------------------------------------------- internals
    @staticmethod
    def _require(uow: Any, company_id: uuid.UUID) -> None:
        from kernel.errors import UnitOfWorkRequired

        if _uow() is not uow:
            raise UnitOfWorkRequired("StockService must be called inside the caller's unit of work")
        uow.ctx.require_company(company_id)

    @staticmethod
    def _sorted(lines: list[Any]) -> list[Any]:
        return sorted(lines, key=lambda x: (str(x.item_id), str(x.warehouse_id), str(x.lot_id or "")))

    @staticmethod
    def _key(uow: Any, company_id: uuid.UUID, line: Any) -> list[Any]:
        return [uow.ctx.tenant_id, company_id, line.item_id, line.warehouse_id]

    def _lock(self, uow: Any, company_id: uuid.UUID, item_id: uuid.UUID, warehouse_id: uuid.UUID) -> dict[str, Any]:
        from kernel.errors import NotFound

        with _cursor(uow) as cur:
            cur.execute(
                "SELECT qty, value, avg_cost, method, allow_negative FROM qa_ref_valuation WHERE tenant_id = %s "
                "AND company_id = %s AND item_id = %s AND warehouse_id = %s FOR UPDATE",
                [uow.ctx.tenant_id, company_id, item_id, warehouse_id],
            )
            row = cur.fetchone()
        if row is None:
            raise NotFound("item not stocked in this warehouse")
        return dict(zip(("qty", "value", "avg", "method", "allow_negative"), row, strict=True))

    def _consume(self, uow: Any, company_id: uuid.UUID, line: Any, row: dict[str, Any]) -> Decimal:
        if row["method"] == "WAC":
            return row["value"] if line.quantity == row["qty"] else q4(line.quantity * row["avg"])
        need, out = line.quantity, Decimal(0)
        with _cursor(uow) as cur:
            cur.execute(
                "SELECT id, unit_cost, remaining_qty, remaining_value FROM qa_ref_layer WHERE tenant_id = %s AND "
                "company_id = %s AND item_id = %s AND warehouse_id = %s AND remaining_qty > 0 ORDER BY id FOR UPDATE",
                self._key(uow, company_id, line),
            )
            layers = cur.fetchall()
            for layer_id, unit_cost, rem_qty, rem_val in layers:
                if need == 0:
                    break
                take = min(need, rem_qty)
                value = rem_val if take == rem_qty else q4(take * unit_cost)
                cur.execute(
                    "UPDATE qa_ref_layer SET remaining_qty = remaining_qty - %s, remaining_value = remaining_value - %s "
                    "WHERE id = %s",
                    [take, value, layer_id],
                )
                need -= take
                out += value
        return out

    def _existing(self, uow: Any, source: Any) -> Any:
        from kernel.interfaces.stock import StockMoveResult

        with _cursor(uow) as cur:
            cur.execute(
                "SELECT id, value FROM qa_ref_move WHERE tenant_id = %s AND source_type = %s AND source_id = %s "
                "ORDER BY line_ref",
                [uow.ctx.tenant_id, source.type, source.id],
            )
            rows = cur.fetchall()
        if not rows:
            return None
        return StockMoveResult(tuple(r[0] for r in rows), sum((abs(r[1]) for r in rows), Decimal(0)))

    @staticmethod
    def _move(
        uow: Any, company_id: uuid.UUID, source: Any, line: Any, qty: Decimal, value: Decimal, day: dt.date
    ) -> uuid.UUID:
        from kernel import ids

        move_id = ids.new_id()
        with _cursor(uow) as cur:
            cur.execute(
                "INSERT INTO qa_ref_move (id, tenant_id, company_id, item_id, warehouse_id, source_type, source_id, "
                "line_ref, qty, value, move_date) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [move_id, uow.ctx.tenant_id, company_id, line.item_id, line.warehouse_id, source.type, source.id,
                 line.line_ref, qty, value, day],
            )  # fmt: skip
        return move_id
