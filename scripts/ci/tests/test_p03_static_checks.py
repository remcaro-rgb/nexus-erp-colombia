"""Forbidden-pattern scan (C2-8) and ADR-004 static checks. Owner: P03. Fixture code is built at runtime so that
this file itself never contains the forbidden tokens."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _support import CI, REPO, load

forbidden = load("forbidden_patterns")
static = load("static_checks")


class ForbiddenPatternsTest(unittest.TestCase):
    def test_T_P03_LINT_each_forbidden_pattern_is_detected(self):
        samples = {
            "TODO": "# " + "TO" + "DO later",
            "FIXME": "# " + "FIX" + "ME",
            "assert True": "assert " + "True",
            "skip without BLK- id": "@pytest.mark." + "skip(reason='later')",
            "float(": "x = " + "float" + "('1.5')",
            "bare except:": "try:\n    pass\nex" + "cept:\n    pass",
            "print(": "pri" + "nt('debug')",
        }
        for rule, code in samples.items():
            hits = forbidden.scan_text(code)
            self.assertIn(rule, [h[1] for h in hits], rule)

    def test_T_P03_LINT_allowed_forms_are_not_flagged(self):
        ok = "\n".join([
            "@pytest.mark." + "skip(reason='BLK-14 DIAN layout unavailable')",
            "from decimal import Decimal", "amount = Decimal('1.50')", "logger.info('x')",
            "try:\n    pass\nexcept ValueError:\n    pass", "self.print_report()",
        ])
        self.assertEqual(forbidden.scan_text(ok), [])


class StaticChecksTest(unittest.TestCase):
    def _check(self, rel: str, code: str) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.py"
            path.write_text(code, encoding="utf-8")
            return static.check_file(path, rel)

    def test_T_P03_LINT_drf_views_must_inherit_secured_bases(self):
        bad = "from rest_framework.views import APIView\nclass V(APIView):\n    pass\n"
        self.assertTrue(any("SEC-VIEW" in v for v in self._check("backend/sales/views.py", bad)))
        fn = "from rest_framework.decorators import api_view\n@api_view(['GET'])\ndef f(r):\n    return r\n"
        self.assertTrue(any("@api_view" in v for v in self._check("backend/sales/views.py", fn)))
        good = "from iam.policy import SecuredViewSet\nclass V(SecuredViewSet):\n    pass\n"
        self.assertEqual(self._check("backend/sales/views.py", good), [])

    def test_T_P03_LINT_raw_sql_only_in_allowlisted_modules(self):
        code = "from django.db import connection\nwith connection.cursor() as c:\n    c.execute('select 1')\n"
        self.assertTrue(any("RAW-SQL" in v for v in self._check("backend/sales/services.py", code)))
        self.assertEqual(self._check("backend/iam/rls/policies.py", code), [])
        self.assertEqual(self._check("backend/sales/migrations/0002_rls.py", code), [])
        self.assertEqual(self._check("backend/sales/tests/test_rls.py", code), [])

    def test_T_P03_LINT_http_clients_only_in_adapters(self):
        code = "import httpx\nfrom requests import get\n"
        self.assertEqual(len([v for v in self._check("backend/sales/services.py", code) if "HTTP-LIB" in v]), 2)
        self.assertEqual(self._check("backend/einvoice/adapters/dian.py", code), [])

    def test_T_P03_LINT_scans_pass_on_the_repository(self):
        for script in ("forbidden_patterns.py", "static_checks.py"):
            proc = subprocess.run([sys.executable, str(CI / script)], capture_output=True, text=True, cwd=REPO)
            self.assertEqual(proc.returncode, 0, f"{script}: {proc.stderr[-2000:]}")


if __name__ == "__main__":
    unittest.main()
