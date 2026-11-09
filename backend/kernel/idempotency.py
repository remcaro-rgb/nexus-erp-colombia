"""Idempotency keys (C2-6, invariant 11).

The record is inserted in the same transaction as the command, so it becomes
visible only when the command commits. A concurrent request with the same key
blocks on the unique index until the first transaction ends, then replays the
stored response (one execution). Same key + different request hash → 422.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from django.db import transaction

from kernel import ids
from kernel.errors import IdempotencyKeyInvalid, IdempotencyKeyReused
from kernel.events import EventJSONEncoder

KEY_RE = re.compile(r"^[A-Za-z0-9._:\-]{8,255}$")


def validate_key(key: str) -> str:
    if not isinstance(key, str) or not KEY_RE.match(key):
        raise IdempotencyKeyInvalid("Idempotency-Key must be 8-255 chars of [A-Za-z0-9._:-]")
    return key


def request_hash(payload: Any, scope: str = "") -> str:
    """SHA-256 over canonical JSON of ``(scope, payload)`` (Decimals as strings)."""
    canonical = json.dumps(
        {"scope": scope, "payload": payload},
        cls=EventJSONEncoder,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class StoredResponse:
    body: Any
    status_code: int


def claim(tenant_id: uuid.UUID, key: str, req_hash: str, scope: str, using: str = "default") -> StoredResponse | None:
    """Claim ``key`` for this transaction.

    Returns ``None`` when this transaction owns the key (execute the command),
    or the stored response to replay. Raises :class:`IdempotencyKeyReused` when
    the key was used with a different request.

    ORM only (ADR-004): ``get_or_create`` inserts inside a savepoint; a concurrent
    insert of the same ``(tenant_id, key)`` blocks on the unique index until the
    first transaction ends, then fails with IntegrityError and re-reads the
    committed row (replay), or succeeds if the first transaction rolled back.
    """
    from kernel.models import IdempotencyRecord

    validate_key(key)
    with transaction.atomic(using=using):
        record, created = IdempotencyRecord.objects.using(using).get_or_create(
            tenant_id=tenant_id,
            key=key,
            defaults={
                "id": ids.new_id(),
                "scope": scope,
                "request_hash": req_hash,
                "response": None,
                "status_code": 0,
                "status": IdempotencyRecord.Status.COMPLETED,
            },
        )
    if created:
        return None
    if record.request_hash != req_hash:
        raise IdempotencyKeyReused("this Idempotency-Key was used with a different request")
    body = json.loads(record.response) if isinstance(record.response, str) else record.response
    return StoredResponse(body=body, status_code=int(record.status_code))


def complete(tenant_id: uuid.UUID, key: str, body: Any, status_code: int, using: str = "default") -> None:
    from kernel.models import IdempotencyRecord

    # Canonical JSON first (Decimals as strings, EventJSONEncoder), then stored through the JSONField.
    canonical = json.loads(json.dumps(body, cls=EventJSONEncoder))
    IdempotencyRecord.objects.using(using).filter(tenant_id=tenant_id, key=key).update(
        response=canonical, status_code=status_code
    )
