"""Runtime configuration sources shared by the settings and ``make db-setup`` (FIX-D-W01-1).

One source of truth for the database and for secrets:

* database: ``DATABASE_URL`` when set; otherwise built from ``ERP_DB_HOST``,
  ``ERP_DB_PORT`` (default 5432), ``ERP_DB_NAME``, ``ERP_DB_USER`` and
  ``ERP_DB_PASSWORD`` or ``ERP_DB_PASSWORD_FILE`` (the variables the DEV compose
  stack passes); neither → :class:`ConfigError` naming the variables.
* secrets: ``NAME`` or ``NAME_FILE`` (file read at start-up, trailing newline
  stripped). Setting both is an error.

Error messages name variables and file paths, never values.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from urllib.parse import quote

from django.core.exceptions import ImproperlyConfigured

#: Variables that select the ``ERP_DB_*`` source (any one of them set → that source is used).
ERP_DB_VARS = ("ERP_DB_HOST", "ERP_DB_PORT", "ERP_DB_NAME", "ERP_DB_USER", "ERP_DB_PASSWORD", "ERP_DB_PASSWORD_FILE")
ERP_DB_REQUIRED = ("ERP_DB_HOST", "ERP_DB_NAME", "ERP_DB_USER")


class ConfigError(ImproperlyConfigured):
    """A required runtime setting is missing or unreadable (message names the variable, never the value)."""


def read_secret(name: str, environ: Mapping[str, str], *, required: bool = True) -> str | None:
    """Value of ``name`` from the environment or from the file named by ``name_FILE``."""
    file_var = f"{name}_FILE"
    value = environ.get(name, "")
    path = environ.get(file_var, "")
    if value and path:
        raise ConfigError(f"set {name} or {file_var}, not both")
    if path:
        try:
            value = Path(path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ConfigError(f"{file_var} points to an unreadable file ({path}): {exc.strerror}") from None
        if not value:
            raise ConfigError(f"{file_var} points to an empty file ({path})")
    if not value:
        if required:
            raise ConfigError(f"{name} is not set (set {name} or {file_var})")
        return None
    return value


def uses_erp_db_vars(environ: Mapping[str, str]) -> bool:
    return not environ.get("DATABASE_URL", "").strip() and any(environ.get(v) for v in ERP_DB_VARS)


def database_url(environ: Mapping[str, str]) -> str:
    """The database URL: ``DATABASE_URL``, else built from ``ERP_DB_*``, else :class:`ConfigError`."""
    url = environ.get("DATABASE_URL", "").strip()
    if url:
        return url
    if not any(environ.get(v) for v in ERP_DB_VARS):
        raise ConfigError(
            "no database configured: set DATABASE_URL, or ERP_DB_HOST, ERP_DB_NAME, ERP_DB_USER and "
            "ERP_DB_PASSWORD (or ERP_DB_PASSWORD_FILE); ERP_DB_PORT defaults to 5432"
        )
    missing = [v for v in ERP_DB_REQUIRED if not environ.get(v, "").strip()]
    if missing:
        raise ConfigError(f"DATABASE_URL is not set and {', '.join(missing)} is missing (ERP_DB_* configuration)")
    port = environ.get("ERP_DB_PORT", "").strip() or "5432"
    if not port.isdigit():
        raise ConfigError("ERP_DB_PORT must be a TCP port number")
    password = read_secret("ERP_DB_PASSWORD", environ) or ""
    host = environ["ERP_DB_HOST"].strip()
    user = quote(environ["ERP_DB_USER"].strip(), safe="")
    name = quote(environ["ERP_DB_NAME"].strip(), safe="")
    return f"postgres://{user}:{quote(password, safe='')}@{host}:{port}/{name}"
