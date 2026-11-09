import json

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import connection

from kernel import boot


class FakeCursor:
    def __init__(self, row):
        self.row = row
        self.sql = None

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchone(self):
        return self.row


def test_T_P01_09_prod_boot_refuses_superuser_and_bypassrls():
    with pytest.raises(boot.UnsafeDatabaseRole, match="SUPERUSER"):
        boot.check_db_role(FakeCursor(("postgres", True, False)))
    with pytest.raises(boot.UnsafeDatabaseRole, match="BYPASSRLS"):
        boot.check_db_role(FakeCursor(("erp_app", False, True)))
    with pytest.raises(boot.UnsafeDatabaseRole):
        boot.check_db_role(FakeCursor(None))
    cur = FakeCursor(("erp_app", False, False))
    assert boot.check_db_role(cur) == "erp_app"
    assert "pg_roles" in cur.sql


@pytest.mark.django_db
def test_T_P01_09_real_role_check_and_connection_hook(settings):
    with connection.cursor() as cur:
        name = boot.check_db_role(cur)  # the CI role is not superuser / BYPASSRLS
    assert name
    settings.ERP_REQUIRE_SAFE_DB_ROLE = True
    boot._on_connection_created(sender=None, connection=connection)
    settings.ERP_REQUIRE_SAFE_DB_ROLE = False
    boot._on_connection_created(sender=None, connection=connection)


def test_T_P01_09_connection_hook_refuses_superuser(settings, monkeypatch):
    settings.ERP_REQUIRE_SAFE_DB_ROLE = True

    class Conn:
        vendor = "postgresql"

        def cursor(self):
            class Ctx:
                def __enter__(self_inner):
                    return FakeCursor(("postgres", True, True))

                def __exit__(self_inner, *a):
                    return False

            return Ctx()

    with pytest.raises(boot.UnsafeDatabaseRole):
        boot._on_connection_created(sender=None, connection=Conn())


def test_T_P01_09_production_settings_validation(tmp_path):
    good_key = "k" * 60
    boot.validate_production_settings(debug=False, secret_key=good_key, fiscal_mode="simulated", gate_records_path="")
    with pytest.raises(ImproperlyConfigured, match="DEBUG"):
        boot.validate_production_settings(
            debug=True, secret_key=good_key, fiscal_mode="simulated", gate_records_path=""
        )
    with pytest.raises(ImproperlyConfigured, match="SECRET_KEY"):
        boot.validate_production_settings(debug=False, secret_key="", fiscal_mode="simulated", gate_records_path="")
    with pytest.raises(ImproperlyConfigured, match="GATE_RECORDS"):
        boot.validate_production_settings(
            debug=False, secret_key=good_key, fiscal_mode="production", gate_records_path=""
        )
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(ImproperlyConfigured, match="unreadable"):
        boot.validate_production_settings(
            debug=False, secret_key=good_key, fiscal_mode="production", gate_records_path=str(bad)
        )
    partial = tmp_path / "partial.json"
    partial.write_text(json.dumps([{"gate": "EG-1", "status": "APPROVED"}]))
    with pytest.raises(ImproperlyConfigured, match="EG-7"):
        boot.validate_production_settings(
            debug=False, secret_key=good_key, fiscal_mode="production", gate_records_path=str(partial)
        )
    full = tmp_path / "full.json"
    full.write_text(json.dumps([{"gate": g, "status": "APPROVED"} for g in ("EG-1", "EG-7", "EG-3")]))
    boot.validate_production_settings(
        debug=False, secret_key=good_key, fiscal_mode="production", gate_records_path=str(full)
    )


def test_T_P01_09_prod_settings_module_refuses_to_boot(monkeypatch):
    import importlib
    import sys

    monkeypatch.setenv("DJANGO_SECRET_KEY", "s" * 60)
    sys.modules.pop("erp.settings.prod", None)
    with pytest.raises(ImproperlyConfigured, match="DEV-only"):
        importlib.import_module("erp.settings.prod")
    monkeypatch.setenv("DJANGO_DEBUG", "true")
    sys.modules.pop("erp.settings.prod", None)
    with pytest.raises(ImproperlyConfigured, match="DEBUG"):
        importlib.import_module("erp.settings.prod")
