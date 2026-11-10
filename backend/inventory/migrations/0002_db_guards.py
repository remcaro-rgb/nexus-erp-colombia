"""DB-level guards for the inventory core (ADR-003, C1-5, D-013, D-014, D-032, F-ACC-BRANCH).

* Composite FK ``(tenant_id, company_id) → iam_company`` on every table (no cross-tenant rows).
* ``inventory_warehouse (tenant_id, company_id, branch_id) → iam_branch``: a warehouse always points to a branch
  of its own company (T-ACC-BRANCH-03).
* ``inventory_location (tenant_id, company_id, warehouse_id) → inventory_warehouse`` and
  ``(warehouse_id, parent_id) → inventory_location (warehouse_id, id)``: the parent is in the same warehouse.
  Trigger ``inventory_location_tree_guard``: no cycles (ancestor walk), parent must be VIEW/INTERNAL.
* Stock rows (moves, quants, packaged quants, reservations) reference the location's warehouse
  (``(warehouse_id, location_id) → inventory_location (warehouse_id, id)``).
* ``inventory_stock_move`` is append-only once posted (kernel immutability trigger).
* ``inventory_stock_quant``: on_hand may be negative only in a warehouse with ``allow_negative``.
* Deferred constraint triggers: Σ(packaged × factor) ≤ quant.on_hand per (item, location, lot) at commit.
* Forced RLS on every table (tenant + company predicate).
"""

from django.db import migrations

from iam import rls
from iam.branches.db import branch_fk_sql
from kernel.db import company_fk_sql, immutable_trigger_sql

TABLES = (
    "inventory_uom",
    "inventory_item",
    "inventory_presentation",
    "inventory_warehouse",
    "inventory_location",
    "inventory_putaway_rule",
    "inventory_lot",
    "inventory_serial",
    "inventory_stock_move",
    "inventory_stock_quant",
    "inventory_packaged_quant",
    "inventory_reservation",
    "inventory_item_valuation",
    "inventory_valuation_layer",
    "inventory_transfer",
    "inventory_transfer_line",
)

FORWARD: list[str] = []
REVERSE: list[str] = []


def add(pair: tuple[str, str]) -> None:
    FORWARD.append(pair[0])
    REVERSE.append(pair[1])


for _t in TABLES:
    add(company_fk_sql(_t))
add(branch_fk_sql("inventory_warehouse"))
add(
    (
        "ALTER TABLE inventory_location ADD CONSTRAINT inventory_location_wh_company_fk "
        "FOREIGN KEY (tenant_id, company_id, warehouse_id) "
        "REFERENCES inventory_warehouse (tenant_id, company_id, id) DEFERRABLE INITIALLY IMMEDIATE;",
        "ALTER TABLE inventory_location DROP CONSTRAINT IF EXISTS inventory_location_wh_company_fk;",
    )
)
add(
    (
        "ALTER TABLE inventory_location ADD CONSTRAINT inventory_location_parent_same_wh_fk "
        "FOREIGN KEY (warehouse_id, parent_id) REFERENCES inventory_location (warehouse_id, id) "
        "DEFERRABLE INITIALLY IMMEDIATE;",
        "ALTER TABLE inventory_location DROP CONSTRAINT IF EXISTS inventory_location_parent_same_wh_fk;",
    )
)
for _t, _col in (
    ("inventory_stock_quant", "location_id"),
    ("inventory_packaged_quant", "location_id"),
    ("inventory_reservation", "location_id"),
    ("inventory_putaway_rule", "location_id"),
):
    _name = f"{_t}_loc_same_wh_fk"[:63]
    add(
        (
            f"ALTER TABLE {_t} ADD CONSTRAINT {_name} FOREIGN KEY (warehouse_id, {_col}) "
            f"REFERENCES inventory_location (warehouse_id, id);",
            f"ALTER TABLE {_t} DROP CONSTRAINT IF EXISTS {_name};",
        )
    )

add(
    (
        """
CREATE OR REPLACE FUNCTION inventory_location_tree_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  cur uuid := NEW.parent_id;
  hops int := 0;
  ptype text;
BEGIN
  IF NEW.parent_id IS NULL THEN
    RETURN NEW;
  END IF;
  SELECT location_type INTO ptype FROM inventory_location WHERE id = NEW.parent_id;
  IF ptype NOT IN ('VIEW', 'INTERNAL') THEN
    RAISE EXCEPTION 'location % cannot be a parent (type %)', NEW.parent_id, ptype
      USING ERRCODE = 'P0001', HINT = 'inventory.location_not_leaf';
  END IF;
  WHILE cur IS NOT NULL LOOP
    IF cur = NEW.id THEN
      RAISE EXCEPTION 'location tree cycle through %', NEW.id
        USING ERRCODE = 'P0001', HINT = 'inventory.location_cycle';
    END IF;
    hops := hops + 1;
    IF hops > 64 THEN
      RAISE EXCEPTION 'location tree too deep at %', NEW.id USING ERRCODE = 'P0001';
    END IF;
    SELECT parent_id INTO cur FROM inventory_location WHERE id = cur;
  END LOOP;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS inventory_location_tree_trg ON inventory_location;
CREATE TRIGGER inventory_location_tree_trg BEFORE INSERT OR UPDATE OF parent_id ON inventory_location
  FOR EACH ROW EXECUTE FUNCTION inventory_location_tree_guard();
""",
        "DROP TRIGGER IF EXISTS inventory_location_tree_trg ON inventory_location;",
    )
)
add(immutable_trigger_sql("inventory_stock_move", "posted"))
add(
    (
        """
CREATE OR REPLACE FUNCTION inventory_quant_negative_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.on_hand < 0 AND NOT EXISTS (
       SELECT 1 FROM inventory_warehouse w WHERE w.id = NEW.warehouse_id AND w.allow_negative) THEN
    RAISE EXCEPTION 'negative stock for item % in location %', NEW.item_id, NEW.location_id
      USING ERRCODE = 'P0001', HINT = 'inventory.stock_insufficient';
  END IF;
  IF NEW.reserved > NEW.on_hand AND NEW.reserved > OLD.reserved AND NOT EXISTS (
       SELECT 1 FROM inventory_warehouse w WHERE w.id = NEW.warehouse_id AND w.allow_negative) THEN
    RAISE EXCEPTION 'reservation exceeds stock for item % in location %', NEW.item_id, NEW.location_id
      USING ERRCODE = 'P0001', HINT = 'inventory.stock_insufficient';
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS inventory_quant_negative_trg ON inventory_stock_quant;
CREATE TRIGGER inventory_quant_negative_trg BEFORE UPDATE ON inventory_stock_quant
  FOR EACH ROW EXECUTE FUNCTION inventory_quant_negative_guard();
""",
        "DROP TRIGGER IF EXISTS inventory_quant_negative_trg ON inventory_stock_quant;",
    )
)
add(
    (
        """
CREATE OR REPLACE FUNCTION inventory_packaged_invariant() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  canonical numeric;
  packaged numeric;
BEGIN
  SELECT coalesce(sum(q.on_hand), 0) INTO canonical FROM inventory_stock_quant q
   WHERE q.item_id = NEW.item_id AND q.location_id = NEW.location_id AND q.lot_id IS NOT DISTINCT FROM NEW.lot_id;
  SELECT coalesce(sum(p.qty_packages * pr.factor), 0) INTO packaged
    FROM inventory_packaged_quant p JOIN inventory_presentation pr ON pr.id = p.presentation_id
   WHERE p.item_id = NEW.item_id AND p.location_id = NEW.location_id AND p.lot_id IS NOT DISTINCT FROM NEW.lot_id;
  IF packaged > greatest(canonical, 0) THEN
    RAISE EXCEPTION 'packaged stock % exceeds canonical % (item %, location %)', packaged, canonical,
      NEW.item_id, NEW.location_id USING ERRCODE = 'P0001', HINT = 'inventory.packaged_exceeds_canonical';
  END IF;
  RETURN NULL;
END;
$$;
DROP TRIGGER IF EXISTS inventory_packaged_invariant_trg ON inventory_packaged_quant;
CREATE CONSTRAINT TRIGGER inventory_packaged_invariant_trg AFTER INSERT OR UPDATE ON inventory_packaged_quant
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION inventory_packaged_invariant();
DROP TRIGGER IF EXISTS inventory_quant_packaged_invariant_trg ON inventory_stock_quant;
CREATE CONSTRAINT TRIGGER inventory_quant_packaged_invariant_trg AFTER UPDATE ON inventory_stock_quant
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION inventory_packaged_invariant();
""",
        "DROP TRIGGER IF EXISTS inventory_packaged_invariant_trg ON inventory_packaged_quant;\n"
        "DROP TRIGGER IF EXISTS inventory_quant_packaged_invariant_trg ON inventory_stock_quant;",
    )
)
for _t in TABLES:
    add(rls.enable_sql(_t))


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0001_initial"),
        ("iam", "0002_security"),
        ("kernel", "0002_rls_kernel_tenant_tables"),
    ]

    operations = [migrations.RunSQL("\n".join(FORWARD), "\n".join(reversed(REVERSE)))]
