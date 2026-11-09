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

from django.db import connections

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
    """
    validate_key(key)
    with connections[using].cursor() as cur:
        cur.execute(
            "INSERT INTO kernel_idempotency "
            "(id, tenant_id, key, scope, request_hash, response, status_code, status, created_at) "
            "VALUES (%s, %s, %s, %s, %s, NULL, 0, 'COMPLETED', now()) "
            "ON CONFLICT (tenant_id, key) DO NOTHING RETURNING id",
            [ids.new_id(), tenant_id, key, scope, req_hash],
        )
        if cur.fetchone() is not None:
            return None
        cur.execute(
            "SELECT request_hash, response, status_code FROM kernel_idempotency WHERE tenant_id = %s AND key = %s",
            [tenant_id, key],
        )
        row = cur.fetchone()
    stored_hash, response, status_code = row
    if stored_hash != req_hash:
        raise IdempotencyKeyReused("this Idempotency-Key was used with a different request")
    body = json.loads(response) if isinstance(response, str) else response
    return StoredResponse(body=body, status_code=int(status_code))


def complete(tenant_id: uuid.UUID, key: str, body: Any, status_code: int, using: str = "default") -> None:
    with connections[using].cursor() as cur:
        cur.execute(
            "UPDATE kernel_idempotency SET response = %s::jsonb, status_code = %s WHERE tenant_id = %s AND key = %s",
            [json.dumps(body, cls=EventJSONEncoder), status_code, tenant_id, key],
        )
