"""DB guards for recipes and transformations (P09c; ADR-003, C1-5, F-INV-BOM, F-INV-XFORM).

* Composite FK ``(tenant_id, company_id) → iam_company`` and forced RLS on every new table.
* ``inventory_transformation (tenant_id, company_id, warehouse_id) → inventory_warehouse``: an order never points
  to a warehouse of another company (``transformation_cross_scope`` is the service-level code).
* Lines of a POSTED or REVERSED order are immutable (values frozen at post; corrections are reversals).
"""

from django.db import migrations

from iam import rls
from kernel.db import company_fk_sql

TABLES = (
    "inventory_bom",
    "inventory_bom_line",
    "inventory_portioning_template",
    "inventory_portioning_template_output",
    "inventory_transformation",
    "inventory_transformation_line",
)

FORWARD: list[str] = []
REVERSE: list[str] = []


def add(pair: tuple[str, str]) -> None:
    FORWARD.append(pair[0])
    REVERSE.append(pair[1])


for _t in TABLES:
    add(company_fk_sql(_t))
add(
    (
        "ALTER TABLE inventory_transformation ADD CONSTRAINT inventory_xform_wh_company_fk "
        "FOREIGN KEY (tenant_id, company_id, warehouse_id) "
        "REFERENCES inventory_warehouse (tenant_id, company_id, id) DEFERRABLE INITIALLY IMMEDIATE;",
        "ALTER TABLE inventory_transformation DROP CONSTRAINT IF EXISTS inventory_xform_wh_company_fk;",
    )
)
add(
    (
        """
CREATE OR REPLACE FUNCTION inventory_xform_line_frozen() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  st text;
BEGIN
  SELECT status INTO st FROM inventory_transformation WHERE id = OLD.order_id;
  IF st IN ('POSTED', 'REVERSED') THEN
    RAISE EXCEPTION 'lines of a % transformation are immutable', st
      USING ERRCODE = 'P0001', HINT = 'kernel.immutable_record';
  END IF;
  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS inventory_xform_line_frozen_trg ON inventory_transformation_line;
CREATE TRIGGER inventory_xform_line_frozen_trg BEFORE UPDATE OR DELETE ON inventory_transformation_line
  FOR EACH ROW EXECUTE FUNCTION inventory_xform_line_frozen();
""",
        "DROP TRIGGER IF EXISTS inventory_xform_line_frozen_trg ON inventory_transformation_line;\n"
        "DROP FUNCTION IF EXISTS inventory_xform_line_frozen();",
    )
)
for _t in TABLES:
    add(rls.enable_sql(_t))


class Migration(migrations.Migration):
    dependencies = [("inventory", "0121_p09c_bom_transformations")]

    operations = [migrations.RunSQL("\n".join(FORWARD), "\n".join(reversed(REVERSE)))]
