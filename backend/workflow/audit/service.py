"""Tamper-evident audit trail (AuditService, owner P07).

* ``record`` runs inside the caller's unit of work (same transaction): the
  audit row commits or rolls back with the business change.
* Per tenant the events form a chain: ``hash = SHA-256(prev_hash + canonical
  event)``; ``seq`` is gap-free per tenant, serialized by a transaction-level
  advisory lock.
* The table is append-only (``kernel_forbid_mutation`` trigger).
* Sensitive values are never stored: secrets are redacted, personal/sensitive
  fields are stored as SHA-256 hashes (equality checks stay possible).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from django.db import connections

from kernel import clock, ids
from kernel.events import to_jsonable
from kernel.interfaces.audit import AuditEntry
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork, current_uow

GENESIS = "0" * 64
REDACTED = "***"
SECRET_WORDS = ("password", "secret", "token", "api_key", "apikey", "private_key", "credential", "otp", "pin")
#: Personal / sensitive fields hashed instead of stored (classification registry, extended by modules).
SENSITIVE_FIELDS: set[str] = {
    "document_number",
    "email",
    "phone",
    "address",
    "birth_date",
    "salary",
    "bank_account",
    "account_number",
    "requester_email",
    "requester_name",
    "health",
}


def register_sensitive_fields(names: Iterable[str]) -> None:
    SENSITIVE_FIELDS.update(names)


def _is_secret(name: str) -> bool:
    low = name.lower()
    return any(w in low for w in SECRET_WORDS)


def _digest(value: Any) -> str:
    raw = json.dumps(to_jsonable(value), sort_keys=True, ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def protect(name: str, value: Any, sensitive: frozenset[str] = frozenset()) -> Any:
    if value is None:
        return None
    if _is_secret(name):
        return REDACTED
    if name in SENSITIVE_FIELDS or name in sensitive:
        return _digest(value)
    return to_jsonable(value)


def diff(
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    *,
    sensitive: Iterable[str] = (),
) -> dict[str, dict[str, Any]]:
    """Changed fields only: ``{field: {"before": .., "after": ..}}`` with protection applied."""
    before, after = dict(before or {}), dict(after or {})
    sens = frozenset(sensitive)
    out: dict[str, dict[str, Any]] = {}
    for key in sorted(set(before) | set(after)):
        b, a = before.get(key), after.get(key)
        if to_jsonable(b) == to_jsonable(a):
            continue
        out[key] = {"before": protect(key, b, sens), "after": protect(key, a, sens)}
    return out


def sanitize_changes(changes: Mapping[str, Any]) -> dict[str, Any]:
    """Apply protection to a caller-built ``changes`` mapping (diff form or flat form)."""
    out: dict[str, Any] = {}
    for key, value in changes.items():
        if isinstance(value, Mapping) and set(value) <= {"before", "after"}:
            out[key] = {k: protect(key, v) for k, v in value.items()}
        else:
            out[key] = protect(key, value)
    return out


def canonical(row: Mapping[str, Any]) -> str:
    fields = {
        "tenant_id": row["tenant_id"],
        "seq": row["seq"],
        "company_id": row["company_id"],
        "at": row["at"],
        "principal_id": row["principal_id"],
        "principal_type": row["principal_type"],
        "actor_id": row["actor_id"],
        "channel": row["channel"],
        "action": row["action"],
        "resource_type": row["resource_type"],
        "resource_id": row["resource_id"],
        "changes": row["changes"],
        "request_id": row["request_id"],
        "trace_id": row["trace_id"],
        "ip": row["ip"],
        "user_agent": row["user_agent"],
        "reason": row["reason"],
    }
    norm = to_jsonable(fields)
    if isinstance(fields["at"], dt.datetime):
        norm["at"] = fields["at"].astimezone(dt.UTC).isoformat(timespec="microseconds")
    return json.dumps(norm, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def chain_hash(prev_hash: str, row: Mapping[str, Any]) -> str:
    return hashlib.sha256((prev_hash + canonical(row)).encode()).hexdigest()


@dataclass(frozen=True)
class RequestMeta:
    request_id: str = ""
    trace_id: str = ""
    ip: str = ""
    user_agent: str = ""


@dataclass(frozen=True)
class VerifyReport:
    ok: bool
    checked: int
    first_bad_seq: int | None = None
    head_hash: str = GENESIS
    reason: str = ""


def _lock_key(tenant_id: uuid.UUID) -> int:
    return int.from_bytes(hashlib.sha256(b"wf_audit:" + tenant_id.bytes).digest()[:8], "big", signed=True)


class DbAuditService:
    """Real ``AuditService`` over ``workflow_audit_event``."""

    def record(
        self,
        uow: UnitOfWork,
        entry: AuditEntry,
        *,
        meta: RequestMeta | None = None,
        retention_class: str = "financial",
    ) -> uuid.UUID:
        from workflow.models import AuditEvent

        ctx: TenantContext = uow.ctx
        meta = meta or RequestMeta(request_id=str(ctx.correlation_id or ""))
        with connections[uow.using].cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", [_lock_key(ctx.tenant_id)])
        last = (
            AuditEvent.objects.using(uow.using)
            .filter(tenant_id=ctx.tenant_id)
            .order_by("-seq")
            .values_list("seq", "hash")
            .first()
        )
        seq, prev = (last[0] + 1, last[1]) if last else (1, GENESIS)
        row: dict[str, Any] = {
            "tenant_id": ctx.tenant_id,
            "seq": seq,
            "company_id": entry.company_id,
            "at": clock.now(),
            "principal_id": ctx.principal_id,
            "principal_type": ctx.principal_type.value,
            "actor_id": ctx.on_behalf_of,
            "channel": ctx.channel.value,
            "action": entry.action[:80],
            "resource_type": entry.resource_type[:80],
            "resource_id": entry.resource_id,
            "changes": sanitize_changes(entry.changes),
            "request_id": meta.request_id[:64],
            "trace_id": meta.trace_id[:64],
            "ip": meta.ip[:45],
            "user_agent": meta.user_agent[:255],
            "reason": entry.reason,
        }
        digest = chain_hash(prev, row)
        event = AuditEvent.objects.using(uow.using).create(
            id=ids.new_id(), prev_hash=prev, hash=digest, retention_class=retention_class, **row
        )
        return event.id

    def verify(self, tenant_id: uuid.UUID, using: str = "default") -> VerifyReport:
        from workflow.models import AuditEvent

        prev, expected_seq, checked = GENESIS, 1, 0
        fields = [
            "seq", "tenant_id", "company_id", "at", "principal_id", "principal_type", "actor_id", "channel",
            "action", "resource_type", "resource_id", "changes", "request_id", "trace_id", "ip", "user_agent",
            "reason", "prev_hash", "hash",
        ]  # fmt: skip
        rows = AuditEvent.objects.using(using).filter(tenant_id=tenant_id).order_by("seq").values(*fields)
        for row in rows.iterator(chunk_size=2000):
            if row["seq"] != expected_seq:
                return VerifyReport(False, checked, row["seq"], prev, "sequence gap")
            if row["prev_hash"] != prev:
                return VerifyReport(False, checked, row["seq"], prev, "prev_hash mismatch")
            if chain_hash(prev, row) != row["hash"]:
                return VerifyReport(False, checked, row["seq"], prev, "hash mismatch")
            prev, expected_seq, checked = row["hash"], expected_seq + 1, checked + 1
        return VerifyReport(True, checked, None, prev)

    def verify_chain(self, tenant_id: uuid.UUID) -> bool:
        return self.verify(tenant_id).ok

    def anchor(self, tenant_id: uuid.UUID, day: dt.date, store: Any, using: str = "default") -> Any:
        """Write the chain head of ``day`` to object storage with a retention lock and record it."""
        from workflow.models import AuditAnchor, AuditEvent

        end = clock.local_midnight_utc(day + dt.timedelta(days=1))
        head = (
            AuditEvent.objects.using(using)
            .filter(tenant_id=tenant_id, at__lt=end)
            .order_by("-seq")
            .values_list("seq", "hash")
            .first()
        )
        last_seq, head_hash = head if head else (0, GENESIS)
        body = {"tenant_id": str(tenant_id), "day": day.isoformat(), "last_seq": last_seq, "head_hash": head_hash}
        raw = json.dumps(body, sort_keys=True).encode()
        anchor_hash = hashlib.sha256(raw).hexdigest()
        stored = store.put(tenant_id, f"audit-anchor-{day.isoformat()}.json", raw, "application/json")
        if hasattr(store, "lock_retention"):
            store.lock_retention(tenant_id, stored.key, until=day.replace(year=day.year + 10))
        anchor, _ = AuditAnchor.objects.using(using).get_or_create(
            tenant_id=tenant_id,
            day=day,
            defaults={"last_seq": last_seq, "head_hash": head_hash, "anchor_hash": anchor_hash, "object_key": stored.key},
        )
        return anchor


def record_change(
    action: str,
    resource_type: str,
    resource_id: uuid.UUID | None,
    *,
    company_id: uuid.UUID | None = None,
    before: Mapping[str, Any] | None = None,
    after: Mapping[str, Any] | None = None,
    sensitive: Iterable[str] = (),
    reason: str = "",
    uow: UnitOfWork | None = None,
) -> uuid.UUID:
    """Convenience used by every workflow service: audit inside the current UoW."""
    from kernel import services
    from kernel.interfaces.audit import AuditService
    from kernel.uow import require_uow

    uow = uow or current_uow() or require_uow()
    svc = services.optional(AuditService) or DbAuditService()
    entry = AuditEntry(
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        company_id=company_id,
        changes=diff(before, after, sensitive=sensitive),
        reason=reason,
    )
    return svc.record(uow, entry)
