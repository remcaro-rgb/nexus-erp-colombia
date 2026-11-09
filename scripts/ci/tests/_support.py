"""Shared helpers for the P03 tests (stdlib only; runnable with `python3 -m unittest` or pytest). Owner: P03."""
from __future__ import annotations

import importlib.util
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
CI = REPO / "scripts" / "ci"
PG_IMAGE = ("docker.io/library/postgres:18.6-alpine@sha256:"
            "123b17998ccaf47e53b502e8c6a1e79afd8f579c6f3eec83c563da026f957395")
CADDY_IMAGE = ("docker.io/library/caddy:2.11.9-alpine@sha256:"
               "0bbb055bda6793c999bde78b1b5ce3e89f829fb031af2cf50056ea1246559fee")


def load(module_name: str):
    """Import a script from scripts/ci by file name (they are scripts, not a package)."""
    path = CI / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(f"p03_{module_name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def require_docker() -> None:
    """Docker is a hard requirement of these tests (they verify containers); fail loudly, never skip."""
    if shutil.which("docker") is None:
        raise AssertionError("docker CLI not found: the P03 container tests need Docker")
    probe = subprocess.run(["docker", "info"], capture_output=True, check=False)
    if probe.returncode != 0:
        raise AssertionError("docker daemon not reachable: start it (scripts/ci/run-tests.sh does) and re-run")


class Postgres:
    """Ephemeral PostgreSQL 18 container with the repo's role init script; no network, tmpfs storage."""

    def __init__(self) -> None:
        self.name = f"nexus-p03-test-{secrets.token_hex(4)}"
        self.passwords = {k: secrets.token_hex(12) for k in (
            "ERP_DB_OWNER_PASSWORD", "ERP_DB_APP_PASSWORD", "ERP_DB_READONLY_PASSWORD",
            "ERP_DB_PLATFORM_PASSWORD", "POSTGRES_PASSWORD")}

    def __enter__(self) -> "Postgres":
        require_docker()
        env = []
        for key, value in self.passwords.items():
            env += ["-e", f"{key}={value}"]
        subprocess.run(["docker", "run", "-d", "--name", self.name, "--network", "none", *env,
                        "-e", "ERP_DB_NAME=erp", "-v", f"{REPO / 'deploy/dev/db/init'}:/docker-entrypoint-initdb.d:ro",
                        "--tmpfs", "/var/lib/postgresql:size=512m", PG_IMAGE], check=True, capture_output=True)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if subprocess.run(["docker", "exec", self.name, "pg_isready", "-h", "127.0.0.1", "-U", "postgres",
                               "-d", "erp"], capture_output=True, check=False).returncode == 0:
                return self
            time.sleep(1)
        self.__exit__(None, None, None)
        raise AssertionError("postgres test container not ready in 120 s")

    def __exit__(self, *exc) -> None:
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True, check=False)

    def sql(self, sql: str, user: str = "postgres", db: str = "erp") -> list[str]:
        """Run SQL as `user` over TCP inside the container (password auth for non-superusers)."""
        env = []
        if user != "postgres":
            key = {"erp_owner": "ERP_DB_OWNER_PASSWORD", "erp_app": "ERP_DB_APP_PASSWORD",
                   "erp_readonly": "ERP_DB_READONLY_PASSWORD", "erp_platform": "ERP_DB_PLATFORM_PASSWORD"}[user]
            env = ["-e", f"PGPASSWORD={self.passwords[key]}"]
        proc = subprocess.run(["docker", "exec", "-i", *env, self.name, "psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1",
                               "-h", "127.0.0.1", "-U", user, "-d", db], input=sql.encode(),
                              capture_output=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.decode(errors="replace"))
        return [line for line in proc.stdout.decode().splitlines() if line]
