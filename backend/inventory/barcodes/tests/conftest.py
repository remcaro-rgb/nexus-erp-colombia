"""Fixtures for F-INV-BARCODE tests: the P09a inventory world (real PostgreSQL, IAM policy) + the DocumentRenderer
contract double (D-005: P17 proves real PDFs at G3)."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from typing import Any

import pytest

from inventory.barcodes import registry as barcode_registry
from kernel import services
from kernel.interfaces.documents import DocumentRenderer
from kernel.testing.fakes import FakeDocumentRenderer

# P09a core test module, imported dynamically: its fixtures are re-exported here (pytest collects module
# attributes of a conftest) and mypy does not type-check the core's excluded test tree through this import.
_base: Any = importlib.import_module("inventory.tests.conftest")
Inv = Any
# P09a fixtures re-exported for this directory (pytest collects module attributes of a conftest)
_issuer_session = _base._issuer_session
clerk = _base.clerk
inv = _base.inv
inventory_services = _base.inventory_services
issuer = _base.issuer
login = _base.login
world = _base.world


@pytest.fixture(autouse=True)
def barcode_services() -> Iterator[dict[str, Any]]:
    renderer: DocumentRenderer = FakeDocumentRenderer()
    with services.override(DocumentRenderer, renderer):
        barcode_registry.register_fields()
        yield {"renderer": renderer}
