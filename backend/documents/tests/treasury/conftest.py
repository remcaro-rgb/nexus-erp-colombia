"""RC / CE documents over P16's real treasury world (real ledger, approvals, AR/AP) with the REAL renderer."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from documents.renderer import RENDERER
from kernel import services
from kernel.interfaces.documents import DocumentRenderer
from treasury.testing.support import ApiClient, World, treasury_world


@pytest.fixture
def world(db: Any, settings: Any) -> Iterator[World]:
    settings.ERP_TENANT_CONTEXT_RESOLVER = "treasury.testing.support.resolve_context"
    renderer: DocumentRenderer = RENDERER
    with treasury_world() as w, services.override(DocumentRenderer, renderer):
        yield w


@pytest.fixture
def api(world: World) -> ApiClient:
    return ApiClient(world)
