"""T-P03-02 (app role cannot bypass RLS) and T-P03-03 (DR drill restores, invariants pass). Owner: P03.
Real PostgreSQL 18 containers, synthetic data only."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _support import CI, Postgres

T1 = "01890000-0000-7000-8000-000000000001"
T2 = "01890000-0000-7000-8000-000000000002"
SCHEMA = f"""
CREATE TABLE demo_item (id serial PRIMARY KEY, tenant_id uuid NOT NULL, name text NOT NULL);
ALTER TABLE demo_item ENABLE ROW LEVEL SECURITY;
ALTER TABLE demo_item FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON demo_item
  USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
  WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
BEGIN; SET LOCAL app.tenant_id = '{T1}';
INSERT INTO demo_item (tenant_id, name) VALUES ('{T1}', 'Artículo sintético A'), ('{T1}', 'Artículo sintético B');
COMMIT;
BEGIN; SET LOCAL app.tenant_id = '{T2}';
INSERT INTO demo_item (tenant_id, name) VALUES ('{T2}', 'Artículo sintético C');
COMMIT;
"""


class RolesTest(unittest.TestCase):
    def test_T_P03_02_app_role_cannot_bypass_rls_and_is_not_owner(self):
        with Postgres() as pg:
            rows = pg.sql("SELECT rolname, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb FROM pg_roles "
                          "WHERE rolname LIKE 'erp_%' ORDER BY 1")
            roles = {r.split("|")[0]: r.split("|")[1:] for r in rows}
            self.assertEqual(set(roles), {"erp_app", "erp_owner", "erp_platform", "erp_readonly"})
            for name, (sup, bypass, createrole, _createdb) in roles.items():
                self.assertEqual((sup, bypass, createrole), ("f", "f", "f"), name)
            self.assertEqual(pg.sql("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname='erp'"),
                             ["erp_owner"])
            self.assertEqual(pg.sql("SELECT nspowner::regrole FROM pg_namespace WHERE nspname='public'"),
                             ["erp_owner"])
            # migrations as owner; the app sees rows only through the tenant binding (SET LOCAL)
            pg.sql(SCHEMA, user="erp_owner")
            self.assertEqual(pg.sql("SELECT count(*) FROM demo_item", user="erp_app"), ["0"])
            self.assertEqual(pg.sql(f"BEGIN; SET LOCAL app.tenant_id = '{T1}'; "
                                    "SELECT count(*) FROM demo_item; COMMIT;", user="erp_app"), ["2"])
            # the binding dies at COMMIT: a reused (pooled) session sees nothing afterwards
            self.assertEqual(pg.sql(f"BEGIN; SET LOCAL app.tenant_id = '{T2}'; COMMIT; "
                                    "SELECT count(*) FROM demo_item;", user="erp_app"), ["0"])
            with self.assertRaises(RuntimeError) as ctx:
                pg.sql(f"BEGIN; SET LOCAL app.tenant_id = '{T1}'; "
                       f"INSERT INTO demo_item (tenant_id, name) VALUES ('{T2}', 'x'); COMMIT;", user="erp_app")
            self.assertIn("row-level security", str(ctx.exception))
            with self.assertRaises(RuntimeError):
                pg.sql("ALTER TABLE demo_item DISABLE ROW LEVEL SECURITY", user="erp_app")
            with self.assertRaises(RuntimeError):
                pg.sql("CREATE TABLE app_owned (id int)", user="erp_app")
            # readonly can read (still under RLS), cannot write
            self.assertEqual(pg.sql("SELECT count(*) FROM demo_item", user="erp_readonly"), ["0"])
            with self.assertRaises(RuntimeError):
                pg.sql(f"BEGIN; SET LOCAL app.tenant_id = '{T1}'; "
                       f"INSERT INTO demo_item (tenant_id, name) VALUES ('{T1}', 'x'); COMMIT;", user="erp_readonly")


class DrDrillTest(unittest.TestCase):
    def _drill(self, container: str) -> tuple[int, dict]:
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "dr.json"
            proc = subprocess.run([sys.executable, str(CI / "dr_drill.py"), "--source-container", container,
                                   "--report", str(report)], capture_output=True, text=True)
            return proc.returncode, json.loads(report.read_text(encoding="utf-8"))

    def test_T_P03_03_dr_drill_restores_into_fresh_container_and_invariants_pass(self):
        with Postgres() as pg:
            pg.sql(SCHEMA, user="erp_owner")
            code, report = self._drill(pg.name)
        self.assertEqual(code, 0, report)
        self.assertTrue(report["ok"])
        self.assertEqual(report["restored_counts"], {"demo_item": 3})
        checks = {c["check"]: c["status"] for c in report["checks"]}
        for name in ("rls_forced_on_tenant_tables", "app_role_no_bypass", "row_counts_equal_source"):
            self.assertEqual(checks[name], "PASSED")
        self.assertIn("restore_seconds", report)

    def test_T_P03_03_dr_drill_fails_when_an_invariant_is_violated(self):
        with Postgres() as pg:
            pg.sql(SCHEMA + "CREATE TABLE leaky (id int, tenant_id uuid);", user="erp_owner")
            code, report = self._drill(pg.name)
        self.assertEqual(code, 1)
        failed = [c for c in report["checks"] if c["status"] != "PASSED"]
        self.assertEqual([c["check"] for c in failed], ["rls_forced_on_tenant_tables"])
        self.assertEqual(failed[0]["detail"], "leaky")


if __name__ == "__main__":
    unittest.main()
