"""P09b tables (F-INV-ISSUE, F-INV-BARCODE) + DB guards.

Lives in ``inventory/issues/migrations`` (P09a extension point: appended to the ``inventory.migrations`` path).
Guards: composite company FK and forced RLS on every table; issue branch → ``iam_branch`` of the same company;
issue warehouse → same-company warehouse; posted issues are immutable (lines frozen, document never deleted,
only the POSTED → REVERSED transition and its reversal fields may change).
"""

import django.db.models.deletion
import kernel.clock
import kernel.fields
import kernel.ids
from django.db import migrations, models

from iam import rls
from iam.branches.db import branch_fk_sql
from kernel.db import company_fk_sql

TABLES = (
    "inventory_barcode",
    "inventory_gs1_prefix",
    "inventory_label_template",
    "inventory_label_print_job",
    "inventory_issue_concept",
    "inventory_stock_issue",
    "inventory_stock_issue_line",
)
FORWARD: list[str] = []
REVERSE: list[str] = []


def add(pair: tuple[str, str]) -> None:
    FORWARD.append(pair[0])
    REVERSE.append(pair[1])


for _t in TABLES:
    add(company_fk_sql(_t))
add(branch_fk_sql("inventory_stock_issue"))
add(
    (
        "ALTER TABLE inventory_stock_issue ADD CONSTRAINT inventory_stock_issue_wh_company_fk "
        "FOREIGN KEY (tenant_id, company_id, warehouse_id) "
        "REFERENCES inventory_warehouse (tenant_id, company_id, id) DEFERRABLE INITIALLY IMMEDIATE;",
        "ALTER TABLE inventory_stock_issue DROP CONSTRAINT IF EXISTS inventory_stock_issue_wh_company_fk;",
    )
)
add(
    (
        """
CREATE OR REPLACE FUNCTION inventory_stock_issue_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.status IN ('POSTED', 'REVERSED') THEN
      RAISE EXCEPTION 'posted stock issue % cannot be deleted', OLD.id USING ERRCODE = 'P0001';
    END IF;
    RETURN OLD;
  END IF;
  IF OLD.status = 'REVERSED' OR (OLD.status = 'POSTED' AND NEW.status NOT IN ('POSTED', 'REVERSED')) THEN
    RAISE EXCEPTION 'posted stock issue % is immutable', OLD.id USING ERRCODE = 'P0001';
  END IF;
  IF OLD.status = 'POSTED' AND (NEW.total IS DISTINCT FROM OLD.total
      OR NEW.journal_entry_id IS DISTINCT FROM OLD.journal_entry_id OR NEW.number <> OLD.number
      OR NEW.warehouse_id <> OLD.warehouse_id OR NEW.concept_id <> OLD.concept_id
      OR NEW.posting_date IS DISTINCT FROM OLD.posting_date) THEN
    RAISE EXCEPTION 'posted stock issue % is immutable (use :reverse)', OLD.id USING ERRCODE = 'P0001';
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS inventory_stock_issue_guard_trg ON inventory_stock_issue;
CREATE TRIGGER inventory_stock_issue_guard_trg BEFORE UPDATE OR DELETE ON inventory_stock_issue
  FOR EACH ROW EXECUTE FUNCTION inventory_stock_issue_guard();
CREATE OR REPLACE FUNCTION inventory_stock_issue_line_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  st text;
BEGIN
  SELECT status INTO st FROM inventory_stock_issue WHERE id = coalesce(NEW.issue_id, OLD.issue_id);
  IF st IN ('POSTED', 'REVERSED') THEN
    RAISE EXCEPTION 'lines of a posted stock issue are immutable' USING ERRCODE = 'P0001';
  END IF;
  RETURN coalesce(NEW, OLD);
END;
$$;
DROP TRIGGER IF EXISTS inventory_stock_issue_line_guard_trg ON inventory_stock_issue_line;
CREATE TRIGGER inventory_stock_issue_line_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON inventory_stock_issue_line
  FOR EACH ROW EXECUTE FUNCTION inventory_stock_issue_line_guard();
""",
        "DROP TRIGGER IF EXISTS inventory_stock_issue_line_guard_trg ON inventory_stock_issue_line;\n"
        "DROP TRIGGER IF EXISTS inventory_stock_issue_guard_trg ON inventory_stock_issue;\n"
        "DROP FUNCTION IF EXISTS inventory_stock_issue_line_guard();\n"
        "DROP FUNCTION IF EXISTS inventory_stock_issue_guard();",
    )
)
for _t in TABLES:
    add(rls.enable_sql(_t))


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0122_p09c_db_guards"),
        ("iam", "0002_security"),
    ]

    operations = [
        migrations.CreateModel(
            name="Barcode",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=kernel.ids.new_id,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("tenant_id", models.UUIDField(db_index=True, editable=False)),
                ("company_id", models.UUIDField(db_index=True, editable=False)),
                (
                    "created_at",
                    models.DateTimeField(default=kernel.clock.now, editable=False),
                ),
                ("created_by", models.UUIDField(editable=False, null=True)),
                (
                    "updated_at",
                    models.DateTimeField(default=kernel.clock.now, editable=False),
                ),
                ("updated_by", models.UUIDField(editable=False, null=True)),
                ("version", models.PositiveIntegerField(default=1, editable=False)),
                ("value", models.CharField(max_length=80)),
                (
                    "symbology",
                    models.CharField(
                        choices=[
                            ("CODE128", "Code128"),
                            ("EAN13", "Ean13"),
                            ("GTIN14_IMPORTED", "Gtin14 Imported"),
                            ("QR", "Qr"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "owner_type",
                    models.CharField(
                        choices=[
                            ("item", "Item"),
                            ("presentation", "Presentation"),
                            ("location", "Location"),
                            ("lot", "Lot"),
                        ],
                        max_length=20,
                    ),
                ),
                ("owner_id", models.UUIDField()),
                ("item_id", models.UUIDField(blank=True, null=True)),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("GENERATED_INTERNAL", "Generated Internal"),
                            ("GENERATED_GS1_PREFIX", "Generated Gs1 Prefix"),
                            ("IMPORTED", "Imported"),
                        ],
                        max_length=24,
                    ),
                ),
                ("gs1_prefix", models.CharField(blank=True, default="", max_length=11)),
                ("gs1_ownership_claimed", models.BooleanField(default=False)),
                ("status", models.CharField(default="ACTIVE", max_length=16)),
            ],
            options={
                "db_table": "inventory_barcode",
                "indexes": [
                    models.Index(
                        fields=["tenant_id", "company_id", "owner_type", "owner_id"],
                        name="inventory_barcode_owner_ix",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("tenant_id", "company_id", "value"),
                        name="inventory_barcode_value_uq",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("gs1_ownership_claimed", False),
                            models.Q(
                                ("source", "GENERATED_GS1_PREFIX"),
                                models.Q(("gs1_prefix", ""), _negated=True),
                            ),
                            _connector="OR",
                        ),
                        name="inventory_barcode_gs1_claim_ck",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="Gs1Prefix",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=kernel.ids.new_id,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("tenant_id", models.UUIDField(db_index=True, editable=False)),
                ("company_id", models.UUIDField(db_index=True, editable=False)),
                (
                    "created_at",
                    models.DateTimeField(default=kernel.clock.now, editable=False),
                ),
                ("created_by", models.UUIDField(editable=False, null=True)),
                (
                    "updated_at",
                    models.DateTimeField(default=kernel.clock.now, editable=False),
                ),
                ("updated_by", models.UUIDField(editable=False, null=True)),
                ("version", models.PositiveIntegerField(default=1, editable=False)),
                ("prefix", models.CharField(max_length=11)),
                ("restricted_circulation", models.BooleanField(default=False)),
                ("next_reference", models.BigIntegerField(default=1)),
            ],
            options={
                "db_table": "inventory_gs1_prefix",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("tenant_id", "company_id", "prefix"),
                        name="inventory_gs1_uq",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="IssueConcept",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=kernel.ids.new_id,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("tenant_id", models.UUIDField(db_index=True, editable=False)),
                ("company_id", models.UUIDField(db_index=True, editable=False)),
                (
                    "created_at",
                    models.DateTimeField(default=kernel.clock.now, editable=False),
                ),
                ("created_by", models.UUIDField(editable=False, null=True)),
                (
                    "updated_at",
                    models.DateTimeField(default=kernel.clock.now, editable=False),
                ),
                ("updated_by", models.UUIDField(editable=False, null=True)),
                ("version", models.PositiveIntegerField(default=1, editable=False)),
                ("code", models.CharField(max_length=40)),
                ("name", models.CharField(max_length=200)),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("CONSUMPTION", "Consumption"),
                            ("DIRECT_CONSUMPTION", "Direct Consumption"),
                            ("SCRAP", "Scrap"),
                            ("SAMPLE", "Sample"),
                            ("DONATION", "Donation"),
                            ("INTERNAL_USE", "Internal Use"),
                            ("OTHER", "Other"),
                        ],
                        default="OTHER",
                        max_length=20,
                    ),
                ),
                ("account_role", models.CharField(max_length=60)),
                ("requires_dimension", models.BooleanField(default=False)),
                ("required_dims", models.JSONField(default=list)),
                ("requires_reason", models.BooleanField(default=False)),
                ("reason_codes", models.JSONField(default=list)),
                ("requires_approval", models.BooleanField(default=False)),
                (
                    "approval_threshold_value",
                    kernel.fields.AmountField(blank=True, null=True),
                ),
                ("allow_negative", models.BooleanField(default=False)),
                ("status", models.CharField(default="ACTIVE", max_length=16)),
            ],
            options={
                "db_table": "inventory_issue_concept",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("tenant_id", "company_id", "code"),
                        name="inventory_issue_concept_code_uq",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("allow_negative", False)),
                        name="inventory_issue_concept_no_negative_ck",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("approval_threshold_value__isnull", True),
                            ("approval_threshold_value__gte", 0),
                            _connector="OR",
                        ),
                        name="inventory_issue_concept_threshold_ck",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="LabelTemplate",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=kernel.ids.new_id,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("tenant_id", models.UUIDField(db_index=True, editable=False)),
                ("company_id", models.UUIDField(db_index=True, editable=False)),
                (
                    "created_at",
                    models.DateTimeField(default=kernel.clock.now, editable=False),
                ),
                ("created_by", models.UUIDField(editable=False, null=True)),
                (
                    "updated_at",
                    models.DateTimeField(default=kernel.clock.now, editable=False),
                ),
                ("updated_by", models.UUIDField(editable=False, null=True)),
                ("version", models.PositiveIntegerField(default=1, editable=False)),
                ("code", models.CharField(max_length=40)),
                ("name", models.CharField(max_length=200)),
                ("symbology", models.CharField(default="CODE128", max_length=10)),
                ("width_mm", models.PositiveSmallIntegerField(default=50)),
                ("height_mm", models.PositiveSmallIntegerField(default=25)),
                ("layout", models.JSONField(default=dict)),
                ("copies", models.PositiveSmallIntegerField(default=1)),
                ("status", models.CharField(default="ACTIVE", max_length=16)),
            ],
            options={
                "db_table": "inventory_label_template",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("tenant_id", "company_id", "code"),
                        name="inventory_label_tpl_code_uq",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("height_mm__gte", 10),
                            ("height_mm__lte", 300),
                            ("width_mm__gte", 10),
                            ("width_mm__lte", 300),
                        ),
                        name="inventory_label_tpl_size_ck",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="StockIssue",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=kernel.ids.new_id,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("tenant_id", models.UUIDField(db_index=True, editable=False)),
                ("company_id", models.UUIDField(db_index=True, editable=False)),
                (
                    "created_at",
                    models.DateTimeField(default=kernel.clock.now, editable=False),
                ),
                ("created_by", models.UUIDField(editable=False, null=True)),
                (
                    "updated_at",
                    models.DateTimeField(default=kernel.clock.now, editable=False),
                ),
                ("updated_by", models.UUIDField(editable=False, null=True)),
                ("version", models.PositiveIntegerField(default=1, editable=False)),
                ("number", models.CharField(blank=True, default="", max_length=40)),
                ("document_date", models.DateField()),
                ("posting_date", models.DateField(blank=True, null=True)),
                ("branch_id", models.UUIDField()),
                ("requested_by", models.UUIDField(blank=True, null=True)),
                ("cost_center_id", models.UUIDField(blank=True, null=True)),
                ("business_unit_id", models.UUIDField(blank=True, null=True)),
                ("aux_abierto_id", models.UUIDField(blank=True, null=True)),
                ("item_dimension_id", models.UUIDField(blank=True, null=True)),
                ("reason", models.CharField(blank=True, max_length=255, null=True)),
                ("notes", models.TextField(blank=True, default="")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("DRAFT", "Draft"),
                            ("SUBMITTED", "Submitted"),
                            ("APPROVED", "Approved"),
                            ("REJECTED", "Rejected"),
                            ("POSTED", "Posted"),
                            ("REVERSED", "Reversed"),
                            ("CANCELLED", "Cancelled"),
                        ],
                        default="DRAFT",
                        max_length=12,
                    ),
                ),
                ("approval_id", models.UUIDField(blank=True, null=True)),
                ("total", kernel.fields.AmountField(blank=True, null=True)),
                ("journal_entry_id", models.UUIDField(blank=True, null=True)),
                ("posted_at", models.DateTimeField(blank=True, null=True)),
                ("reversal_entry_id", models.UUIDField(blank=True, null=True)),
                (
                    "reversal_reason",
                    models.CharField(blank=True, default="", max_length=2000),
                ),
                ("reversed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "concept",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="inventory.issueconcept",
                    ),
                ),
                (
                    "warehouse",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="inventory.warehouse",
                    ),
                ),
            ],
            options={
                "db_table": "inventory_stock_issue",
            },
        ),
        migrations.CreateModel(
            name="StockIssueLine",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=kernel.ids.new_id,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("tenant_id", models.UUIDField(db_index=True, editable=False)),
                ("company_id", models.UUIDField(db_index=True, editable=False)),
                ("line_no", models.PositiveIntegerField()),
                (
                    "description",
                    models.CharField(blank=True, default="", max_length=255),
                ),
                ("qty_packages", kernel.fields.QtyField(blank=True, null=True)),
                ("quantity", kernel.fields.QtyField()),
                ("uom", models.CharField(max_length=20)),
                ("cost_center_id", models.UUIDField(blank=True, null=True)),
                ("business_unit_id", models.UUIDField(blank=True, null=True)),
                ("aux_abierto_id", models.UUIDField(blank=True, null=True)),
                ("item_dimension_id", models.UUIDField(blank=True, null=True)),
                ("unit_cost", kernel.fields.UnitCostField(blank=True, null=True)),
                ("value", kernel.fields.AmountField(blank=True, null=True)),
                (
                    "issue",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lines",
                        to="inventory.stockissue",
                    ),
                ),
                (
                    "item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="inventory.item",
                    ),
                ),
                (
                    "location",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="inventory.location",
                    ),
                ),
                (
                    "lot",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="inventory.lot",
                    ),
                ),
                (
                    "presentation",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="inventory.presentation",
                    ),
                ),
            ],
            options={
                "db_table": "inventory_stock_issue_line",
            },
        ),
        migrations.CreateModel(
            name="LabelPrintJob",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=kernel.ids.new_id,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("tenant_id", models.UUIDField(db_index=True, editable=False)),
                ("company_id", models.UUIDField(db_index=True, editable=False)),
                (
                    "created_at",
                    models.DateTimeField(default=kernel.clock.now, editable=False),
                ),
                ("created_by", models.UUIDField(editable=False, null=True)),
                (
                    "updated_at",
                    models.DateTimeField(default=kernel.clock.now, editable=False),
                ),
                ("updated_by", models.UUIDField(editable=False, null=True)),
                ("version", models.PositiveIntegerField(default=1, editable=False)),
                ("targets", models.JSONField(default=dict)),
                ("copies", models.PositiveSmallIntegerField(default=1)),
                ("labels", models.JSONField(default=list)),
                ("label_count", models.PositiveIntegerField(default=0)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("QUEUED", "Queued"),
                            ("RENDERED", "Rendered"),
                            ("DOWNLOADED", "Downloaded"),
                        ],
                        default="QUEUED",
                        max_length=12,
                    ),
                ),
                ("data_hash", models.CharField(blank=True, default="", max_length=64)),
                ("pdf_hash", models.CharField(blank=True, default="", max_length=64)),
                (
                    "source_type",
                    models.CharField(blank=True, default="", max_length=80),
                ),
                ("source_id", models.UUIDField(blank=True, null=True)),
                (
                    "template",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="inventory.labeltemplate",
                    ),
                ),
            ],
            options={
                "db_table": "inventory_label_print_job",
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("copies__gte", 1), ("copies__lte", 500)),
                        name="inventory_label_copies_ck",
                    )
                ],
            },
        ),
        migrations.AddIndex(
            model_name="stockissue",
            index=models.Index(
                fields=["tenant_id", "company_id", "document_date"],
                name="inventory_issue_date_ix",
            ),
        ),
        migrations.AddConstraint(
            model_name="stockissueline",
            constraint=models.UniqueConstraint(
                fields=("issue", "line_no"), name="inventory_issue_line_uq"
            ),
        ),
        migrations.AddConstraint(
            model_name="stockissueline",
            constraint=models.CheckConstraint(
                condition=models.Q(("quantity__gt", 0)),
                name="inventory_issue_line_qty_ck",
            ),
        ),
        migrations.RunSQL("\n".join(FORWARD), "\n".join(reversed(REVERSE))),
    ]
