"""FIX-D-W01-1: one source of truth for the database and the field-encryption key.

DATABASE_URL wins; ERP_DB_* (incl. ERP_DB_PASSWORD_FILE) are used when it is absent; a clear
ImproperlyConfigured names the missing variable — never a value.
"""

import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

import environ
import pytest
from django.core.exceptions import ImproperlyConfigured

from erp.settings.base import field_encryption_key
from kernel import devdb, envconfig

BACKEND = Path(__file__).resolve().parents[2]
PW = "s3:cr/et@p%w"  # synthetic test value with URL-special characters
KEY = base64.urlsafe_b64encode(bytes(range(32))).decode()


def _db(environ_map):
    return environ.Env.db_url_config(envconfig.database_url(environ_map))


def test_FIX_D_W01_1_database_url_wins_over_erp_db_vars():
    cfg = _db({"DATABASE_URL": "postgres://u1:p1@h1:6000/d1", "ERP_DB_HOST": "db", "ERP_DB_NAME": "erp"})
    assert (cfg["HOST"], cfg["PORT"], cfg["NAME"], cfg["USER"]) == ("h1", 6000, "d1", "u1")


def test_FIX_D_W01_1_erp_db_vars_used_when_database_url_absent():
    env = {
        "ERP_DB_HOST": "db",
        "ERP_DB_PORT": "5433",
        "ERP_DB_NAME": "erp",
        "ERP_DB_USER": "erp_app",
        "ERP_DB_PASSWORD": PW,
    }
    cfg = _db(env)
    assert cfg["ENGINE"] == "django.db.backends.postgresql"
    assert (cfg["HOST"], cfg["PORT"], cfg["NAME"], cfg["USER"], cfg["PASSWORD"]) == ("db", 5433, "erp", "erp_app", PW)
    env.pop("ERP_DB_PORT")
    assert _db(env)["PORT"] == 5432


def test_FIX_D_W01_1_erp_db_password_file(tmp_path):
    secret = tmp_path / "db_password"
    secret.write_text(PW + "\n", encoding="utf-8")
    env = {"ERP_DB_HOST": "db", "ERP_DB_NAME": "erp", "ERP_DB_USER": "erp_owner", "ERP_DB_PASSWORD_FILE": str(secret)}
    assert _db(env)["PASSWORD"] == PW
    with pytest.raises(ImproperlyConfigured, match="ERP_DB_PASSWORD or ERP_DB_PASSWORD_FILE, not both"):
        envconfig.database_url({**env, "ERP_DB_PASSWORD": PW})
    with pytest.raises(ImproperlyConfigured, match="ERP_DB_PASSWORD_FILE points to an unreadable file"):
        envconfig.database_url({**env, "ERP_DB_PASSWORD_FILE": str(tmp_path / "missing")})


def test_FIX_D_W01_1_clear_error_when_no_database_configured():
    with pytest.raises(ImproperlyConfigured, match="set DATABASE_URL, or ERP_DB_HOST") as exc:
        envconfig.database_url({})
    assert "ERP_DB_PASSWORD" in str(exc.value)
    with pytest.raises(ImproperlyConfigured, match="ERP_DB_USER is missing"):
        envconfig.database_url({"ERP_DB_HOST": "db", "ERP_DB_NAME": "erp", "ERP_DB_PASSWORD": PW})
    with pytest.raises(ImproperlyConfigured, match="ERP_DB_PASSWORD is not set") as exc:
        envconfig.database_url({"ERP_DB_HOST": "db", "ERP_DB_NAME": "erp", "ERP_DB_USER": "erp_app"})
    with pytest.raises(ImproperlyConfigured, match="ERP_DB_PORT"):
        envconfig.database_url({"ERP_DB_HOST": "db", "ERP_DB_NAME": "e", "ERP_DB_USER": "u", "ERP_DB_PORT": "x"})


def test_FIX_D_W01_1_encryption_key_from_env_or_file(tmp_path):
    assert field_encryption_key({"ERP_FIELD_ENCRYPTION_KEY": KEY}, required=True) == KEY
    key_file = tmp_path / "field-encryption.key"
    key_file.write_text(KEY + "\n", encoding="utf-8")
    assert field_encryption_key({"ERP_FIELD_ENCRYPTION_KEY_FILE": str(key_file)}, required=True) == KEY
    assert field_encryption_key({}, required=False) is None


def test_FIX_D_W01_1_encryption_key_clear_error_never_prints_value(tmp_path):
    with pytest.raises(ImproperlyConfigured, match="ERP_FIELD_ENCRYPTION_KEY is not set") as exc:
        field_encryption_key({}, required=True)
    assert "ERP_FIELD_ENCRYPTION_KEY_FILE" in str(exc.value)
    bad = "not-a-valid-key-" + "x" * 20
    with pytest.raises(ImproperlyConfigured, match="url-safe base64 of 32 bytes") as exc:
        field_encryption_key({"ERP_FIELD_ENCRYPTION_KEY": bad}, required=True)
    assert bad not in str(exc.value)
    empty = tmp_path / "empty.key"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ImproperlyConfigured, match="ERP_FIELD_ENCRYPTION_KEY_FILE points to an empty file"):
        field_encryption_key({"ERP_FIELD_ENCRYPTION_KEY_FILE": str(empty)}, required=True)


def _load_dev_settings(env):
    # Expected values travel in FIXTEST_* variables so that no tracebacks can echo them.
    code = (
        "import json, os, sys; from erp.settings import dev; d = dev.DATABASES['default'];"
        "sys.stdout.write(json.dumps({'host': d['HOST'], 'user': d['USER'], 'name': d['NAME'],"
        " 'pw_ok': d['PASSWORD'] == os.environ['FIXTEST_PW'],"
        " 'key_ok': dev.ERP_FIELD_ENCRYPTION_KEY == os.environ['FIXTEST_KEY']}))"
    )
    keep = {k: v for k, v in os.environ.items() if not k.startswith(("ERP_", "DATABASE_URL", "DJANGO_"))}
    full = {**keep, **env, "FIXTEST_PW": PW, "FIXTEST_KEY": KEY}
    return subprocess.run(  # noqa: S603 - fixed argv, test only
        [sys.executable, "-c", code], cwd=BACKEND, env=full, capture_output=True, text=True, check=False
    )


def test_FIX_D_W01_1_dev_profile_boots_from_compose_variables(tmp_path):
    """The variables deploy/dev/compose.yaml passes are enough for the dev settings (the D-W01-1 defect)."""
    pw_file = tmp_path / "pw"
    pw_file.write_text(PW, encoding="utf-8")
    env = {"ERP_DB_HOST": "db", "ERP_DB_PORT": "5432", "ERP_DB_NAME": "erp", "ERP_DB_USER": "erp_owner",
           "ERP_DB_PASSWORD_FILE": str(pw_file), "ERP_FIELD_ENCRYPTION_KEY": KEY}  # fmt: skip
    proc = _load_dev_settings(env)
    assert proc.returncode == 0, proc.stderr[-1500:]
    assert json.loads(proc.stdout) == {"host": "db", "user": "erp_owner", "name": "erp", "pw_ok": True, "key_ok": True}
    proc = _load_dev_settings({k: v for k, v in env.items() if k != "ERP_FIELD_ENCRYPTION_KEY"})
    assert proc.returncode != 0 and "ERP_FIELD_ENCRYPTION_KEY is not set" in proc.stderr
    assert PW not in proc.stderr
    proc = _load_dev_settings({"ERP_FIELD_ENCRYPTION_KEY": KEY})
    assert proc.returncode != 0 and "set DATABASE_URL, or ERP_DB_HOST" in proc.stderr
    assert KEY not in proc.stderr


def test_FIX_D_W01_1_db_setup_bootstraps_tcp_role_through_admin_url(monkeypatch, capsys):
    calls = []

    class Proc:
        def __init__(self, out=""):
            self.returncode, self.stdout, self.stderr = 0, out, ""

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        sql = kwargs["input"]
        if "rolsuper" in sql:
            return Proc("false,false")
        return Proc("")

    monkeypatch.setattr(devdb.subprocess, "run", fake_run)
    monkeypatch.setenv("DATABASE_URL", f"postgres://nexus_ci:{quote(PW, safe='')}@127.0.0.1:5432/nexus_erp")
    monkeypatch.setenv("ERP_DB_ADMIN_URL", "postgres://postgres@127.0.0.1:5432/postgres")
    assert devdb.main([]) == 0
    scripts = [kw["input"] for _, kw in calls]
    assert any('CREATE ROLE "nexus_ci" LOGIN CREATEDB NOSUPERUSER NOBYPASSRLS PASSWORD' in s for s in scripts)
    assert any('CREATE DATABASE "nexus_erp" OWNER "nexus_ci"' in s for s in scripts)
    for argv, kw in calls:  # the password never travels in argv and is never printed
        assert PW not in " ".join(argv)
        assert kw["env"]["PGHOST"] == "127.0.0.1" and kw["env"]["PGUSER"] == "postgres"
    assert PW not in capsys.readouterr().out
