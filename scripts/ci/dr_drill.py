#!/usr/bin/env python3
"""DR drill (P03 deliverable 6, T-P03-03): restore a backup into a FRESH, isolated PostgreSQL container and run
the invariant checks. Owner: P03. DEV-only; never touches the source database except to read a dump.

  dr_drill.py [--dump FILE | --source-container NAME] [--db erp] [--report reports/dr/dr-drill.json]

Default source: the `db` container of the running DEV stack (project nexus-erp-dev).
Steps: pg_dump -Fc (or given dump) -> new postgres container (--network none, random throwaway passwords,
same role init script) -> pg_restore --exit-on-error -> invariants (deploy/dr/invariants.d/*.sql) ->
per-table row-count parity with the source -> JSON report with measured duration (RTO sample) -> container removed.
Exit 0 only when restore succeeded and every invariant PASSED.
"""
from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INIT_DIR = REPO / "deploy" / "dev" / "db" / "init"
INVARIANTS = REPO / "deploy" / "dr" / "invariants.d"
PG_IMAGE = ("docker.io/library/postgres:18.6-alpine@sha256:"
            "123b17998ccaf47e53b502e8c6a1e79afd8f579c6f3eec83c563da026f957395")
COUNT_SQL = ("SELECT format('%s|%s', c.relname, (xpath('/row/n/text()', query_to_xml("
             "format('select count(*) as n from public.%I', c.relname), false, true, '')))[1]::text) "
             "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace AND n.nspname='public' "
             "WHERE c.relkind IN ('r','p') ORDER BY 1")


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, **kw)


def psql(container: str, db: str, sql: str) -> list[str]:
    out = run(["docker", "exec", "-i", container, "psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1",
               "-U", "postgres", "-d", db], input=sql.encode())
    return [line for line in out.stdout.decode().splitlines() if line]


def default_source() -> str:
    out = run(["docker", "ps", "--filter", "label=com.docker.compose.project=nexus-erp-dev",
               "--filter", "label=com.docker.compose.service=db", "--format", "{{.Names}}"])
    names = out.stdout.decode().split()
    if not names:
        raise SystemExit("dr-drill: no running DEV db container; start it with make dev-up or pass --dump")
    return names[0]


def start_fresh(name: str) -> None:
    env = []
    for var in ("ERP_DB_OWNER_PASSWORD", "ERP_DB_APP_PASSWORD", "ERP_DB_READONLY_PASSWORD",
                "ERP_DB_PLATFORM_PASSWORD", "POSTGRES_PASSWORD"):
        env += ["-e", f"{var}={secrets.token_hex(16)}"]  # throwaway, never stored or printed
    run(["docker", "run", "-d", "--name", name, "--network", "none", "--label", "nexus.dr-drill=true",
         *env, "-e", "ERP_DB_NAME=erp", "-v", f"{INIT_DIR}:/docker-entrypoint-initdb.d:ro",
         "--tmpfs", "/var/lib/postgresql:size=2g", PG_IMAGE])
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        probe = subprocess.run(["docker", "exec", name, "pg_isready", "-h", "127.0.0.1", "-U", "postgres", "-d", "erp"],
                               capture_output=True, check=False)
        if probe.returncode == 0:
            return
        time.sleep(1)
    raise SystemExit("dr-drill: fresh restore target did not become ready in 120 s")


def counts(container: str, db: str) -> dict[str, int]:
    return {row.split("|")[0]: int(row.split("|")[1]) for row in psql(container, db, COUNT_SQL + ";")}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", type=Path)
    ap.add_argument("--source-container")
    ap.add_argument("--db", default="erp")
    ap.add_argument("--report", type=Path, default=REPO / "reports" / "dr" / "dr-drill.json")
    ap.add_argument("--keep", action="store_true", help="keep the restore container for inspection")
    args = ap.parse_args(argv[1:])

    t0 = time.monotonic()
    report: dict = {"drill": "dr-drill", "environment": "dev", "synthetic_data": True}
    source = None
    with tempfile.TemporaryDirectory(prefix="nexus-dr-") as tmp:
        dump = args.dump
        if dump is None:
            source = args.source_container or default_source()
            dump = Path(tmp) / "erp.dump"
            with dump.open("wb") as fh:
                subprocess.run(["docker", "exec", source, "pg_dump", "-U", "postgres", "-Fc", "-d", args.db],
                               stdout=fh, check=True)
            report["source"] = {"container": source, "counts": counts(source, args.db)}
        report["dump_bytes"] = dump.stat().st_size
        target = f"nexus-dr-drill-{secrets.token_hex(4)}"
        try:
            start_fresh(target)
            t_restore = time.monotonic()
            run(["docker", "cp", str(dump), f"{target}:/tmp/erp.dump"])
            run(["docker", "exec", target, "pg_restore", "-U", "postgres", "-d", "erp", "--exit-on-error",
                 "--single-transaction", "/tmp/erp.dump"])
            report["restore_seconds"] = round(time.monotonic() - t_restore, 2)
            checks = []
            for sql_file in sorted(INVARIANTS.glob("*.sql")):
                for row in psql(target, "erp", sql_file.read_text(encoding="utf-8")):
                    name, status, detail = (row.split("|") + ["", ""])[:3]
                    checks.append({"file": sql_file.name, "check": name, "status": status, "detail": detail})
            restored = counts(target, "erp")
            if source:
                parity = restored == report["source"]["counts"]
                checks.append({"file": "row-count-parity", "check": "row_counts_equal_source",
                               "status": "PASSED" if parity else "FAILED",
                               "detail": "" if parity else json.dumps(restored)})
            report["restored_counts"] = restored
            report["checks"] = checks
        except subprocess.CalledProcessError as exc:
            report["error"] = (exc.stderr or b"").decode(errors="replace")[-800:]
            report["checks"] = report.get("checks", [])
        finally:
            if not args.keep:
                subprocess.run(["docker", "rm", "-f", target], capture_output=True, check=False)
    report["total_seconds"] = round(time.monotonic() - t0, 2)
    report["ok"] = "error" not in report and bool(report["checks"]) and all(
        c["status"] == "PASSED" for c in report["checks"])
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    sys.stdout.write(json.dumps({k: report[k] for k in ("ok", "total_seconds") if k in report}
                                | {"failed": [c["check"] for c in report["checks"] if c["status"] != "PASSED"],
                                   "report": str(args.report)}) + "\n")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
