"""Local DEV/CI database bootstrap (``make db-setup``).

The database is resolved like the settings do (:mod:`kernel.envconfig`):
``DATABASE_URL``, else ``ERP_DB_*``, else the local unix socket.

* unix socket (no host, or a host that is a directory): creates a PostgreSQL
  role named after the OS user — LOGIN CREATEDB, NOSUPERUSER, NOBYPASSRLS — and
  the ``nexus_erp`` database, using peer auth (no password exists, nothing
  secret is written). Uses ``sudo -u postgres`` only for this bootstrap.
* TCP (e.g. the PostgreSQL service container of GitHub Actions): when
  ``ERP_DB_ADMIN_URL`` is set, connects with it and creates the role of the
  database URL (same flags, with its password if the URL has one) and its
  database; then refuses a SUPERUSER/BYPASSRLS role. Without an admin URL the
  role and database are assumed to exist (skipping).

Passwords travel only through the ``PGPASSWORD`` environment of ``psql`` and
psql variables on stdin; nothing secret is printed.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from typing import Any
from urllib.parse import unquote, urlsplit

from kernel.envconfig import database_url

ROLE_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, check=False)  # noqa: S603


def _psql(sql: str) -> str:
    res = _run(["sudo", "-n", "-u", "postgres", "psql", "-v", "ON_ERROR_STOP=1", "-qtAc", sql])
    if res.returncode != 0:
        raise SystemExit(f"db-setup: psql failed: {res.stderr.strip()}")
    return res.stdout.strip()


def _wait_ready() -> None:
    if shutil.which("pg_isready") is None:
        return
    if _run(["pg_isready", "-q"]).returncode != 0:
        _run(["sudo", "-n", "service", "postgresql", "start"])
    for _ in range(20):
        if _run(["pg_isready", "-q"]).returncode == 0:
            return
        time.sleep(0.5)
    raise SystemExit("db-setup: PostgreSQL is not accepting connections")


def _url_parts(url: str) -> dict[str, Any]:
    parts = urlsplit(url)
    query = dict(q.split("=", 1) for q in parts.query.split("&") if "=" in q)
    host = unquote(parts.hostname or query.get("host", ""))
    return {
        "host": host,
        "port": str(parts.port or 5432),
        "user": unquote(parts.username or ""),
        "password": unquote(parts.password or ""),
        "dbname": unquote(parts.path.lstrip("/")),
    }


def _is_local_socket(host: str) -> bool:
    return not host or host.startswith("/")


def _admin_psql(admin: dict[str, Any], sql: str, variables: dict[str, str] | None = None) -> str:
    """Run ``sql`` (stdin) as the admin; values go in psql variables (stdin), password in PGPASSWORD."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    env.update(
        PGHOST=admin["host"], PGPORT=admin["port"], PGUSER=admin["user"], PGDATABASE=admin["dbname"] or "postgres"
    )
    if admin["password"]:
        env["PGPASSWORD"] = admin["password"]
    script = "".join(f"\\set {k} '{v.replace(chr(39), chr(39) * 2)}'\n" for k, v in (variables or {}).items()) + sql
    res = subprocess.run(
        ["psql", "-X", "-v", "ON_ERROR_STOP=1", "-qtA", "-f", "-"],  # noqa: S607
        input=script,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if res.returncode != 0:
        raise SystemExit(f"db-setup: psql (admin) failed: {res.stderr.strip()[:300]}")
    return res.stdout.strip()


def _network_bootstrap(target: dict[str, Any], admin_url: str) -> int:
    admin = _url_parts(admin_url)
    role, dbname = target["user"], target["dbname"]
    if not ROLE_RE.match(role) or not ROLE_RE.match(dbname):
        raise SystemExit(f"db-setup: unsupported role/database name {role!r}/{dbname!r}")
    pw = {"pw": target["password"]} if target["password"] else {}
    exists = _admin_psql(admin, f"SELECT 1 FROM pg_roles WHERE rolname = '{role}';\n")  # noqa: S608 - ROLE_RE
    if exists != "1":
        password = " PASSWORD :'pw'" if pw else ""
        _admin_psql(admin, f'CREATE ROLE "{role}" LOGIN CREATEDB NOSUPERUSER NOBYPASSRLS{password};\n', pw)
    elif pw:
        _admin_psql(admin, f"ALTER ROLE \"{role}\" PASSWORD :'pw';\n", pw)
    flag_sql = f"SELECT rolsuper::text || ',' || rolbypassrls::text FROM pg_roles WHERE rolname = '{role}';\n"  # noqa: S608
    flags = _admin_psql(admin, flag_sql)
    if flags != "false,false":
        raise SystemExit(f"db-setup: role {role!r} is superuser/BYPASSRLS; refusing")
    if _admin_psql(admin, f"SELECT 1 FROM pg_database WHERE datname = '{dbname}';\n") != "1":  # noqa: S608
        _admin_psql(admin, f'CREATE DATABASE "{dbname}" OWNER "{role}";\n')
    sys.stdout.write(
        f"db-setup: role {role} ready on {target['host']}:{target['port']} (LOGIN CREATEDB, not superuser)\n"
    )
    return 0


def main(argv: list[str]) -> int:
    configured = bool(os.environ.get("DATABASE_URL", "").strip()) or any(
        os.environ.get(v) for v in ("ERP_DB_HOST", "ERP_DB_NAME", "ERP_DB_USER")
    )
    if configured:
        target = _url_parts(database_url(os.environ))
        if not _is_local_socket(target["host"]):
            admin_url = os.environ.get("ERP_DB_ADMIN_URL", "").strip()
            if admin_url:
                return _network_bootstrap(target, admin_url)
            sys.stdout.write("db-setup: TCP database configured, no ERP_DB_ADMIN_URL; skipping (role must exist)\n")
            return 0
    role = argv[0] if argv else os.environ.get("USER", "")
    if not ROLE_RE.match(role):
        raise SystemExit(f"db-setup: unsupported role name {role!r}")
    _wait_ready()
    exists = _psql(f"SELECT 1 FROM pg_roles WHERE rolname = '{role}'")  # noqa: S608 - role matches ROLE_RE
    if exists != "1":
        _psql(f'CREATE ROLE "{role}" LOGIN CREATEDB NOSUPERUSER NOBYPASSRLS')
    flags = _psql(f"SELECT rolsuper::text || ',' || rolbypassrls::text FROM pg_roles WHERE rolname = '{role}'")  # noqa: S608
    if flags != "false,false":
        raise SystemExit(f"db-setup: role {role!r} is superuser/BYPASSRLS; refusing")
    if _psql("SELECT 1 FROM pg_database WHERE datname = 'nexus_erp'") != "1":
        _psql(f'CREATE DATABASE nexus_erp OWNER "{role}"')
    sys.stdout.write(f"db-setup: role {role} ready (LOGIN CREATEDB, not superuser)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
