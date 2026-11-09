from decimal import Decimal

import pytest
from django.db import connection, models

from kernel import fields


class PrecisionProbe(models.Model):
    id = fields.uuid7_pk()
    amount = fields.AmountField()
    qty = fields.QtyField()
    unit_cost = fields.UnitCostField()
    fx = fields.FxRateField()
    pct = fields.PctField()
    currency = fields.CurrencyField()

    class Meta:
        app_label = "kernel"
        db_table = "kernel_test_precision_probe"
        managed = False


@pytest.fixture
def probe_table(django_db_blocker):
    with django_db_blocker.unblock(), connection.schema_editor() as editor:
        editor.create_model(PrecisionProbe)
    yield
    with django_db_blocker.unblock(), connection.schema_editor() as editor:
        editor.delete_model(PrecisionProbe)


@pytest.mark.django_db(transaction=True)
def test_T_P01_02_field_precision_round_trip(probe_table, raw_sql):
    values = {
        "amount": Decimal("9999999999999999.9999"),
        "qty": Decimal("99999999999999.999999"),
        "unit_cost": Decimal("9999999999999999.99999999"),
        "fx": Decimal("99999999999999.9999999999"),
        "pct": Decimal("999.999999"),
    }
    row = PrecisionProbe.objects.create(**values)
    loaded = PrecisionProbe.objects.get(pk=row.pk)
    for name, value in values.items():
        assert getattr(loaded, name) == value
    assert loaded.currency == "COP"
    assert row.pk.version == 7
    types = dict(
        raw_sql(
            "SELECT column_name, numeric_precision || ',' || numeric_scale FROM information_schema.columns "
            "WHERE table_name = 'kernel_test_precision_probe' AND data_type = 'numeric'"
        )
    )
    assert types == {"amount": "20,4", "qty": "20,6", "unit_cost": "24,8", "fx": "24,10", "pct": "9,6"}


def test_T_P01_02_fields_deconstruct_fixed_precision():
    for cls, digits, places in [
        (fields.AmountField, 20, 4),
        (fields.QtyField, 20, 6),
        (fields.UnitCostField, 24, 8),
        (fields.FxRateField, 24, 10),
        (fields.PctField, 9, 6),
    ]:
        f = cls(max_digits=5, decimal_places=1)
        assert (f.max_digits, f.decimal_places) == (digits, places)
        _, _, _, kwargs = f.deconstruct()
        assert "max_digits" not in kwargs and "decimal_places" not in kwargs
    _, _, _, kwargs = fields.CurrencyField().deconstruct()
    assert "max_length" not in kwargs
