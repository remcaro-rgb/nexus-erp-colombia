"""Provider / signing secrets: references only, resolved in the worker, redacted everywhere (ADR-012, F-PT-OPEN).

* The database stores a **reference** (``secret://einvoice/<name>``), never a value. The API accepts the
  reference write-only and never returns it (T-PT-OPEN-02).
* ``resolve(ref)`` reads ``<ERP_EINVOICE_SECRETS_DIR>/<name>`` (default ``deploy/dev/secrets/einvoice``,
  gitignored, files mode 0600). Every resolved value is registered with the redactor.
* ``redact(text)`` replaces every registered value (and its reference) by ``***``. A logging filter
  (:class:`RedactingFilter`, installed on the root logger by ``einvoice.wiring``) applies it to every log
  record; adapters pass error texts through it before they reach the transmission log or the API.
"""

from __future__ import annotations

import logging
import os
import re
import secrets as std_secrets
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from django.conf import settings

from kernel.errors import ValidationFailed

PREFIX: Final = "secret://einvoice/"
_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")
MASK: Final = "***"
DEV_SIGNING_KEY_REF: Final = PREFIX + "dev-fixture-signing-key"

_lock = threading.Lock()
_known: set[str] = set()


@dataclass(frozen=True, slots=True)
class Secret:
    """A resolved secret. ``repr``/``str`` never show the value."""

    ref: str
    _value: str

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"Secret(ref={MASK})"

    __str__ = __repr__


def validate_ref(ref: str) -> str:
    ref = str(ref or "").strip()
    if not ref.startswith(PREFIX) or not _NAME.match(ref[len(PREFIX) :]):
        raise ValidationFailed(
            f"secret references must look like {PREFIX}<name> (lower-case, digits, . _ -); values are never accepted",
            code="field_forbidden",
        )
    return ref


def secrets_dir() -> Path:
    configured = getattr(settings, "ERP_EINVOICE_SECRETS_DIR", None) or os.environ.get("ERP_EINVOICE_SECRETS_DIR")
    if configured:
        return Path(configured)
    return Path(settings.BASE_DIR).parent / "deploy" / "dev" / "secrets" / "einvoice"


def register_sensitive(value: str) -> None:
    """Make ``value`` redacted in logs/errors (values shorter than 4 characters are not tracked)."""
    if value and len(value) >= 4:
        with _lock:
            _known.add(value)


def resolve(ref: str) -> Secret | None:
    """Value behind ``ref`` or ``None`` when the file does not exist (operator has not provided it)."""
    ref = validate_ref(ref)
    path = secrets_dir() / ref[len(PREFIX) :]
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValidationFailed(f"secret {MASK} unreadable ({exc.strerror})", code="provider_not_configured") from None
    if not value:
        return None
    register_sensitive(value)
    register_sensitive(ref)
    return Secret(ref, value)


def ensure_dev_fixture(ref: str = DEV_SIGNING_KEY_REF) -> Secret:
    """DEV-only fixture key (generated once into the gitignored secrets dir, mode 0600, never printed)."""
    found = resolve(ref)
    if found is not None:
        return found
    directory = secrets_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / ref[len(PREFIX) :]
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(std_secrets.token_hex(32))
    resolved = resolve(ref)
    if resolved is None:  # pragma: no cover - the file was just written
        raise ValidationFailed("dev fixture key could not be created", code="certificate_missing")
    return resolved


def redact(text: Any) -> str:
    out = str(text)
    with _lock:
        known = sorted(_known, key=len, reverse=True)
    for value in known:
        if value in out:
            out = out.replace(value, MASK)
    return out


def redact_obj(obj: Any) -> Any:
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [redact_obj(v) for v in obj]
    return obj


class RedactingFilter(logging.Filter):
    """Redacts registered secret values from every log record (message, args, extra fields)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not _known:
            return True
        try:
            message = record.getMessage()
        except (TypeError, ValueError):
            message = str(record.msg)
        record.msg = redact(message)
        record.args = None
        for key, value in list(record.__dict__.items()):
            if key not in _STD_ATTRS and isinstance(value, str | dict | list | tuple):
                setattr(record, key, redact_obj(value))
        return True


_STD_ATTRS = frozenset(vars(logging.LogRecord("x", 0, "x", 0, "x", None, None)).keys()) | {"message", "asctime"}


def install_log_filter() -> None:
    """Attach the filter to the root logger's handlers and to the root logger (idempotent)."""
    root = logging.getLogger()
    if not any(isinstance(f, RedactingFilter) for f in root.filters):
        root.addFilter(RedactingFilter())
    for handler in root.handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())
