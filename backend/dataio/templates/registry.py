"""Registry of import/export templates (F-X-DATAIO; ``Datos › Plantillas``)."""

from __future__ import annotations

import importlib

from dataio.errors import TemplateUnknown
from dataio.templates.base import Template

#: modules holding the adapters; each exposes ``TEMPLATES`` (a tuple of Template subclasses)
MODULES = (
    "dataio.templates.ledger",
    "dataio.templates.iam",
    "dataio.templates.treasury",
    "dataio.templates.inventory",
    "dataio.templates.sales",
    "dataio.templates.payroll",
    "dataio.templates.tax",
    "dataio.templates.purchasing",
)

_cache: dict[str, Template] = {}


def all_templates() -> dict[str, Template]:
    if not _cache:
        for name in MODULES:
            mod = importlib.import_module(name)
            for cls in mod.TEMPLATES:
                _cache[cls.entity] = cls()
    return dict(sorted(_cache.items()))


def get(entity: str) -> Template:
    tpl = all_templates().get(str(entity))
    if tpl is None:
        raise TemplateUnknown(f"unknown template {entity!r}")
    return tpl
