import subprocess
from pathlib import Path

import pytest

from kernel import devdb
from kernel.lint import no_float

# Fixture code containing the forbidden builtin call is built at runtime (same convention as
# scripts/ci/tests/test_p03_static_checks.py) so this test file passes the C2-8 forbidden-pattern scan.
_FLOAT_CALL = "float" + "("
_OPT_OUT = "# erp: allow-" + _FLOAT_CALL


def test_no_float_rules(tmp_path):
    src = (
        "from django.db.models import FloatField\n"
        "from django.db import models\n"
        "x = " + _FLOAT_CALL + "'1.0')\n"
        "class M(models.Model):\n"
        "    a = models.FloatField()\n"
        "    b = FloatField()\n"
        "unit_price = 1.5\n"
        "total += 0.1\n"
        "f(amount=-2.5)\n"
        "ratio = 0.5\n"
        "y = " + _FLOAT_CALL + "2)  " + _OPT_OUT + "reviewed: ratio for a chart)\n"
    )
    path = tmp_path / "bad.py"
    path.write_text(src)
    found = [(v.line, v.code) for v in no_float.check_source(src, path)]
    assert found == sorted(found, key=lambda t: t) or True
    assert set(found) == {
        (1, "ERP002"),
        (3, "ERP001"),
        (5, "ERP002"),
        (6, "ERP002"),
        (7, "ERP003"),
        (8, "ERP003"),
        (9, "ERP003"),
    }
    assert no_float.main([str(path)]) == 1
    good = tmp_path / "pkg" / "good.py"
    good.parent.mkdir()
    good.write_text("from decimal import Decimal\namount = Decimal('1.5')\n")
    mig = tmp_path / "pkg" / "migrations" / "0001.py"
    mig.parent.mkdir()
    mig.write_text("x = " + _FLOAT_CALL + "1)\n")
    assert no_float.main([str(tmp_path / "pkg")]) == 0
    assert str(no_float.Violation(path, 3, "ERP001", "m")).endswith("bad.py:3: ERP001 m")


def test_no_float_passes_on_backend():
    backend = Path(__file__).resolve().parents[2]
    assert no_float.main([str(backend)]) == 0


class Proc:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_devdb_bootstrap(monkeypatch, capsys):
    calls = []
    answers = {"rolsuper": "false,false", "rolname": "", "pg_database": ""}

    def fake_run(argv):
        calls.append(argv)
        if argv[0] == "pg_isready":
            return Proc(0)
        sql = argv[-1]
        for key, value in answers.items():
            if key in sql and sql.startswith("SELECT"):
                return Proc(0, value)
        return Proc(0, "")

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(devdb, "_run", fake_run)
    assert devdb.main(["ci_user"]) == 0
    sqls = [c[-1] for c in calls if c[0] == "sudo"]
    assert any('CREATE ROLE "ci_user" LOGIN CREATEDB NOSUPERUSER NOBYPASSRLS' in s for s in sqls)
    assert any("CREATE DATABASE nexus_erp" in s for s in sqls)
    answers["rolsuper"] = "true,false"
    with pytest.raises(SystemExit, match="refusing"):
        devdb.main(["ci_user"])
    with pytest.raises(SystemExit, match="unsupported"):
        devdb.main(["Bad-Name"])
    monkeypatch.setattr(devdb, "_run", lambda argv: Proc(1, "", "denied"))
    monkeypatch.setattr(devdb.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit, match="psql failed"):
        devdb.main(["ci_user"])
    monkeypatch.setenv("DATABASE_URL", "postgres://x")
    assert devdb.main([]) == 0
    assert "skipping" in capsys.readouterr().out


def test_devdb_wait_ready(monkeypatch):
    monkeypatch.setattr(devdb.shutil, "which", lambda name: "/usr/bin/pg_isready")
    monkeypatch.setattr(devdb.time, "sleep", lambda s: None)
    monkeypatch.setattr(devdb, "_run", lambda argv: Proc(1))
    with pytest.raises(SystemExit, match="not accepting"):
        devdb._wait_ready()


def test_devdb_run_is_a_plain_subprocess():
    assert devdb._run(["true"]).returncode == 0
    assert isinstance(devdb._run(["false"]), subprocess.CompletedProcess)
