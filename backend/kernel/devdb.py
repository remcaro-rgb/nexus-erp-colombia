"""Local DEV/CI database bootstrap (``make db-setup``).

Creates a PostgreSQL role named after the OS user — LOGIN CREATEDB,
NOSUPERUSER, NOBYPASSRLS — and the ``nexus_erp`` database, using peer auth on
the unix socket (no password exists, nothing secret is written). Skipped when
``DATABASE_URL`` is set. Uses ``sudo -u postgres`` only for this bootstrap.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time

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


def main(argv: list[str]) -> int:
    if os.environ.get("DATABASE_URL"):
        sys.stdout.write("db-setup: DATABASE_URL set; skipping\n")
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
