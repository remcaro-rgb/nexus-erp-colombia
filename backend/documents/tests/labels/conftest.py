"""Label PDFs over P09's real inventory world (items, barcodes, label templates, print jobs) with the REAL
``DocumentRenderer`` of P17 (D-005; the P09 tests use the contract double)."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from typing import Any

import pytest

from documents.renderer import RENDERER
from inventory.barcodes import registry as barcode_registry
from kernel import services
from kernel.interfaces.documents import DocumentRenderer

_base: Any = importlib.import_module("inventory.tests.conftest")
_issuer_session = _base._issuer_session
clerk = _base.clerk
inv = _base.inv
inventory_services = _base.inventory_services
issuer = _base.issuer
login = _base.login
world = _base.world


@pytest.fixture(autouse=True)
def real_renderer() -> Iterator[DocumentRenderer]:
    renderer: DocumentRenderer = RENDERER
    with services.override(DocumentRenderer, renderer):
        barcode_registry.register_fields()
        yield renderer
