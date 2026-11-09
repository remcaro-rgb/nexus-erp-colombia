"""Boot-time safety checks (T-P01-09).

* The database role must not be superuser nor BYPASSRLS (ADR-003); checked on
  every new connection when ``ERP_REQUIRE_SAFE_DB_ROLE`` is true.
* Production boot validations: DEBUG off, secrets present, and
  ``FISCAL_MODE=production`` only with gate records.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.db.backends.signals import connection_created

ROLE_QUERY = "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"

#: External gates (shared/09) that must be APPROVED before fiscal production mode:
#: EG-1 habilitación and EG-7 legal sign-off, plus a signing route: EG-2 (direct
#: certificate) or EG-3 (technology provider).
REQUIRED_FISCAL_GATES = ("EG-1", "EG-7")
SIGNING_ROUTE_GATES = ("EG-2", "EG-3")


class UnsafeDatabaseRole(ImproperlyConfigured):
    pass


def check_db_role(cursor: Any) -> str:
    """Raise :class:`UnsafeDatabaseRole` if the current role is superuser/BYPASSRLS."""
    cursor.execute(ROLE_QUERY)
    row = cursor.fetchone()
    if row is None:
        raise UnsafeDatabaseRole("cannot determine the current database role")
    name, is_super, bypass = row
    if is_super:
        raise UnsafeDatabaseRole(f"database role {name!r} is SUPERUSER; refusing to boot")
    if bypass:
        raise UnsafeDatabaseRole(f"database role {name!r} has BYPASSRLS; refusing to boot")
    return str(name)


def _on_connection_created(sender: Any, connection: Any, **kwargs: Any) -> None:
    from django.conf import settings

    if connection.vendor != "postgresql" or not getattr(settings, "ERP_REQUIRE_SAFE_DB_ROLE", True):
        return
    with connection.cursor() as cursor:
        check_db_role(cursor)


def connect_db_role_check() -> None:
    connection_created.connect(_on_connection_created, dispatch_uid="kernel.boot.db_role_check")


def validate_production_settings(*, debug: bool, secret_key: str, fiscal_mode: str, gate_records_path: str) -> None:
    problems: list[str] = []
    if debug:
        problems.append("DEBUG must be off")
    if len(secret_key) < 50:
        problems.append("DJANGO_SECRET_KEY missing or too short")
    if fiscal_mode == "production":
        problems.extend(_check_gate_records(gate_records_path))
    if problems:
        raise ImproperlyConfigured("production boot refused: " + "; ".join(problems))


def _check_gate_records(path: str) -> list[str]:
    if not path or not Path(path).is_file():
        return ["FISCAL_MODE=production requires ERP_FISCAL_GATE_RECORDS"]
    try:
        records = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["gate records file unreadable"]
    approved = {r.get("gate") for r in records if isinstance(r, dict) and r.get("status") == "APPROVED"}
    missing = [g for g in REQUIRED_FISCAL_GATES if g not in approved]
    if not approved.intersection(SIGNING_ROUTE_GATES):
        missing.append("EG-2|EG-3")
    return [f"external gates not approved: {', '.join(missing)}"] if missing else []
