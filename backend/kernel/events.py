"""Domain event envelope (shared/02 §2).

``{event_id, event_type, occurred_at, tenant_id, company_id, actor: {type, id,
on_behalf_of}, correlation_id, causation_id, payload, schema_version}``.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from kernel import clock, ids
from kernel.errors import ValidationFailed
from kernel.money import Money
from kernel.tenancy import TenantContext

EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.v[1-9][0-9]*$")


class EventJSONEncoder(json.JSONEncoder):
    """Decimals as strings, never floats; UUIDs and datetimes as ISO strings."""

    def default(self, o: Any) -> Any:
        if isinstance(o, Decimal):
            return str(o)
        if isinstance(o, uuid.UUID):
            return str(o)
        if isinstance(o, dt.datetime | dt.date):
            return o.isoformat()
        if isinstance(o, Money):
            return o.to_json()
        if isinstance(o, set | frozenset):
            return sorted(o, key=str)
        return super().default(o)


def to_jsonable(value: Any) -> Any:
    """Round-trip through the encoder so stored JSON never contains floats."""
    if isinstance(value, float):
        raise ValidationFailed("floats are not allowed in event payloads")
    if isinstance(value, dict):
        for v in value.values():
            to_jsonable(v)
    elif isinstance(value, list | tuple):
        for v in value:
            to_jsonable(v)
    return json.loads(json.dumps(value, cls=EventJSONEncoder))


def validate_event_type(event_type: str) -> str:
    if not EVENT_TYPE_RE.match(event_type):
        raise ValidationFailed(f"event_type {event_type!r} must match <module>.<entity>.<verb>.v<N>")
    return event_type


def schema_version_of(event_type: str) -> int:
    return int(event_type.rsplit(".v", 1)[1])


@dataclass(frozen=True, slots=True)
class DomainEvent:
    event_type: str
    payload: dict[str, Any]
    tenant_id: uuid.UUID
    company_id: uuid.UUID | None
    actor_type: str
    actor_id: uuid.UUID
    on_behalf_of: uuid.UUID | None = None
    correlation_id: uuid.UUID | None = None
    causation_id: uuid.UUID | None = None
    event_id: uuid.UUID = field(default_factory=ids.new_id)
    occurred_at: dt.datetime = field(default_factory=clock.now)

    @classmethod
    def build(
        cls,
        ctx: TenantContext,
        event_type: str,
        payload: dict[str, Any],
        *,
        company_id: uuid.UUID | None = None,
        causation_id: uuid.UUID | None = None,
    ) -> DomainEvent:
        validate_event_type(event_type)
        if company_id is not None:
            ctx.require_company(company_id)
        return cls(
            event_type=event_type,
            payload=to_jsonable(payload),
            tenant_id=ctx.tenant_id,
            company_id=company_id,
            actor_type=ctx.principal_type.value,
            actor_id=ctx.principal_id,
            on_behalf_of=ctx.on_behalf_of,
            correlation_id=ctx.correlation_id,
            causation_id=causation_id,
        )

    @property
    def module(self) -> str:
        return self.event_type.split(".", 1)[0]

    def envelope(self) -> dict[str, Any]:
        return {
            "event_id": str(self.event_id),
            "event_type": self.event_type,
            "occurred_at": self.occurred_at.isoformat(),
            "tenant_id": str(self.tenant_id),
            "company_id": str(self.company_id) if self.company_id else None,
            "actor": {
                "type": self.actor_type,
                "id": str(self.actor_id),
                "on_behalf_of": str(self.on_behalf_of) if self.on_behalf_of else None,
            },
            "correlation_id": str(self.correlation_id) if self.correlation_id else None,
            "causation_id": str(self.causation_id) if self.causation_id else None,
            "payload": self.payload,
            "schema_version": schema_version_of(self.event_type),
        }
