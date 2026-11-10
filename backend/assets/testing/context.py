"""Test-only tenant context resolver for the assets API tests (``ERP_TENANT_CONTEXT_RESOLVER``)."""

from __future__ import annotations

import uuid
from typing import Any

_contexts: dict[str, Any] = {}


def register(ctx: Any) -> str:
    """Store ``ctx`` and return the ``X-Test-Context`` key that selects it."""
    key = str(uuid.uuid4())
    _contexts[key] = ctx
    return key


def resolve_context(request: Any) -> Any:
    """The context registered under the request's ``X-Test-Context`` header (``None`` → 401)."""
    return _contexts.get(request.headers.get("X-Test-Context", ""))
