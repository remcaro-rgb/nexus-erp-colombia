"""DB-level guards (C1-5, DIM-R04/R05).

* Composite FK ``(company_id, dimension, parent_id) → (company_id, dimension, id)``:
  a parent is always of the same company and dimension.
* Composite FKs from every journal-line slot to ``(company_id, dimension, id)``
  through constant generated columns: a line never references a node of another
  company or of another dimension; used nodes cannot be deleted.
* ``ledger_journal_line`` is append-only; ``ledger_journal_entry`` allows only
  ``posted → reversed`` (+ ``reversed_by_id``) and never DELETE.
* A node referenced by a journal line cannot change ``node_type`` or ``company_id``.
"""

from django.db import migrations

from kernel.db import immutable_trigger_sql

SLOTS = (
    ("cost_center_id", "cost_center", "cc"),
    ("business_unit_id", "business_unit", "bu"),
    ("aux_abierto_id", "aux_abierto", "aux"),
    ("item_dimension_id", "item_contable", "itd"),
)

FORWARD = [
    "ALTER TABLE ledger_dimension_node ADD CONSTRAINT ledger_dim_parent_fk "
    "FOREIGN KEY (company_id, dimension, parent_id) "
    "REFERENCES ledger_dimension_node (company_id, dimension, id) DEFERRABLE INITIALLY IMMEDIATE;",
]
REVERSE = ["ALTER TABLE ledger_dimension_node DROP CONSTRAINT IF EXISTS ledger_dim_parent_fk;"]

for slot, dim, short in SLOTS:
    FORWARD.append(
        f"ALTER TABLE ledger_journal_line ADD COLUMN {short}_dimension varchar(20) "
        f"GENERATED ALWAYS AS ('{dim}'::varchar(20)) STORED;"
    )
    FORWARD.append(
        f"ALTER TABLE ledger_journal_line ADD CONSTRAINT ledger_line_{short}_fk "
        f"FOREIGN KEY (company_id, {short}_dimension, {slot}) "
        f"REFERENCES ledger_dimension_node (company_id, dimension, id);"
    )
    REVERSE.append(f"ALTER TABLE ledger_journal_line DROP CONSTRAINT IF EXISTS ledger_line_{short}_fk;")
    REVERSE.append(f"ALTER TABLE ledger_journal_line DROP COLUMN IF EXISTS {short}_dimension;")

line_fwd, line_rev = immutable_trigger_sql("ledger_journal_line", None)
FORWARD.append(line_fwd)
REVERSE.append(line_rev)

FORWARD.append(
    """
CREATE OR REPLACE FUNCTION ledger_entry_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'immutable row in %: DELETE is not allowed (use a reversal)', TG_TABLE_NAME
      USING ERRCODE = 'P0001', HINT = 'kernel.immutable_record';
  END IF;
  IF OLD.status <> 'posted' OR NEW.status <> 'reversed' OR OLD.reversed_by_id IS NOT NULL
     OR (to_jsonb(NEW) - 'status' - 'reversed_by_id') IS DISTINCT FROM (to_jsonb(OLD) - 'status' - 'reversed_by_id') THEN
    RAISE EXCEPTION 'immutable row in %: only posted -> reversed is allowed', TG_TABLE_NAME
      USING ERRCODE = 'P0001', HINT = 'kernel.immutable_record';
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS ledger_journal_entry_guard_trg ON ledger_journal_entry;
CREATE TRIGGER ledger_journal_entry_guard_trg BEFORE UPDATE OR DELETE ON ledger_journal_entry
  FOR EACH ROW EXECUTE FUNCTION ledger_entry_guard();
"""
)
REVERSE.append("DROP TRIGGER IF EXISTS ledger_journal_entry_guard_trg ON ledger_journal_entry;")

FORWARD.append(
    """
CREATE OR REPLACE FUNCTION ledger_dimension_node_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW.node_type IS DISTINCT FROM OLD.node_type OR NEW.company_id IS DISTINCT FROM OLD.company_id
      OR NEW.dimension IS DISTINCT FROM OLD.dimension)
     AND EXISTS (
       SELECT 1 FROM ledger_journal_line l
        WHERE l.company_id = OLD.company_id
          AND OLD.id IN (l.cost_center_id, l.business_unit_id, l.aux_abierto_id, l.item_dimension_id)
     ) THEN
    RAISE EXCEPTION 'dimension node % is used by posted lines', OLD.id
      USING ERRCODE = 'P0001', HINT = 'ledger.dimension_in_use';
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS ledger_dimension_node_guard_trg ON ledger_dimension_node;
CREATE TRIGGER ledger_dimension_node_guard_trg BEFORE UPDATE ON ledger_dimension_node
  FOR EACH ROW EXECUTE FUNCTION ledger_dimension_node_guard();
"""
)
REVERSE.append("DROP TRIGGER IF EXISTS ledger_dimension_node_guard_trg ON ledger_dimension_node;")


class Migration(migrations.Migration):
    dependencies = [("ledger", "0001_initial")]

    operations = [migrations.RunSQL("\n".join(FORWARD), "\n".join(reversed(REVERSE)))]
