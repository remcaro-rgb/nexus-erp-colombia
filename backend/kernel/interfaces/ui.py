"""UI shell data (owner: P04 iam). Feeds ``/api/v1/ui/context`` and ``/api/v1/ui/modules`` (FIX-UI-LOGIN-W03).

The kernel serves both endpoints but must not import ``iam`` (layering). IAM registers an implementation
of :class:`UiShellProvider` at app ready; without one ``/ui/modules`` fails closed (503 ``service_unavailable``)
and ``/ui/context`` carries no principal data.

* ``context_extras`` — the principal's part of the UI context: ``companies[]`` (active memberships only,
  ``id, legal_name, nit, code``), ``roles[]``, ``permissions[]`` (effective catalog codes plus resolved
  registry navigation codes, the same list as ``/api/v1/iam/me``), ``user {display_name, email}``,
  ``session_idle_minutes`` and ``feature_flags``.
* ``modules`` — ``{groups, modules}`` of ``docs/spec/ui/module-registry.json`` filtered server-side by the
  principal's permissions: unauthorized modules, submenus, tabs, quick actions, KPIs and panels are absent.
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol, runtime_checkable

from kernel.tenancy import TenantContext


@runtime_checkable
class UiShellProvider(Protocol):
    def context_extras(self, ctx: TenantContext, company_id: uuid.UUID | None) -> dict[str, Any]: ...

    def modules(self, ctx: TenantContext, company_id: uuid.UUID | None) -> dict[str, Any]: ...
