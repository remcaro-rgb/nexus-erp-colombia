"""Resource bridge for P11a's portal hooks (until CCR P11b-4 is applied in ``purchasing.interfaces``).

``portal_submit_quote`` authorizes ``portal.supplier_quote.create`` with the *RFQ* as resource and
``portal_acknowledge_order`` authorizes ``portal.supplier_purchase_order.update`` with ``{"supplier_id": …}``. P04's
engine decides portal ownership on ``company_id`` + ``party_id`` of the resource (``PolicyEngine.portal_owns``), which
neither resource carries, so the real engine denies every real portal principal (P11a's own tests use a grant-only
policy double).

:func:`portal_resources` wraps exactly one such call: for the current execution context only (``ContextVar``), the
registered ``PolicyService`` sees those two P11a resources translated into the ``{company_id, party_id}`` the engine
expects — the RFQ is owned when the portal principal's supplier is invited, the order when it is that supplier's.
The real engine still makes the decision (portal permission, kind, company and party); nothing is granted that the
token does not already own. Every other call, thread or request is delegated unchanged. The swap of the registry
entry is serialized by a lock and always restored.
"""

from __future__ import annotations

import contextvars
import threading
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from kernel import services
from kernel.interfaces.policy import Decision, FieldMask, PolicyService
from kernel.tenancy import TenantContext
from purchasing.models import Rfq, Supplier

_active: contextvars.ContextVar[Supplier | None] = contextvars.ContextVar("purchasing_portal_bridge", default=None)
_lock = threading.Lock()
BRIDGED = ("portal.supplier_quote.create", "portal.supplier_purchase_order.update")


class _BridgePolicy:
    """Delegating ``PolicyService``; translates the two P11a portal resources while :data:`_active` is set."""

    def __init__(self, inner: PolicyService) -> None:
        self.inner = inner

    def _translate(self, action: str, resource: Any) -> Any:
        supplier = _active.get()
        if supplier is None or action not in BRIDGED:
            return resource
        if isinstance(resource, Rfq):
            invited = resource.company_id == supplier.company_id and str(supplier.id) in resource.invited_vendors
            return {"company_id": resource.company_id, "party_id": supplier.party_id if invited else None}
        if isinstance(resource, Mapping) and set(resource) == {"supplier_id"}:
            mine = uuid.UUID(str(resource["supplier_id"])) == supplier.id
            return {"company_id": supplier.company_id, "party_id": supplier.party_id if mine else None}
        return resource

    def authorize(self, ctx: TenantContext, action: str, resource: Any = None) -> Decision:
        return self.inner.authorize(ctx, action, self._translate(action, resource))

    def require(self, ctx: TenantContext, action: str, resource: Any = None) -> None:
        self.inner.require(ctx, action, self._translate(action, resource))

    def scope_queryset(self, ctx: TenantContext, action: str, queryset: Any) -> Any:
        return self.inner.scope_queryset(ctx, action, queryset)

    def field_mask(self, ctx: TenantContext, resource_type: str) -> FieldMask:
        return self.inner.field_mask(ctx, resource_type)

    def policy_version(self, ctx: TenantContext) -> int:
        return self.inner.policy_version(ctx)


@contextmanager
def portal_resources(supplier: Supplier) -> Iterator[None]:
    token = _active.set(supplier)
    try:
        with _lock:
            current = services.get(PolicyService)
            if isinstance(current, _BridgePolicy):
                yield
                return
            with services.override(PolicyService, _BridgePolicy(current)):
                yield
    finally:
        _active.reset(token)
