"""Shipped contract data read at run time: the permission catalog and the UI module registry.

Both live at their repository paths under ``settings.REPO_DIR`` (``/`` in the backend image, where the
Dockerfile copies them read-only; FIX-UI-LOGIN-W03 UL-1). Loaded once per process.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

import yaml
from django.conf import settings

CATALOG_PATH = "contracts/permissions/catalog.yaml"
REGISTRY_PATH = "docs/spec/ui/module-registry.json"


def repo_path(relative: str) -> Path:
    return Path(getattr(settings, "REPO_DIR", Path(__file__).resolve().parents[3])) / relative


@functools.cache
def catalog_codes() -> tuple[str, ...]:
    """Permission codes of ``contracts/permissions/catalog.yaml`` (empty when not shipped)."""
    path = repo_path(CATALOG_PATH)
    if not path.exists():
        return ()
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return tuple(sorted({p["code"] for p in doc.get("codes", []) if isinstance(p, dict) and "code" in p}))


@functools.cache
def module_registry() -> dict[str, Any] | None:
    """``docs/spec/ui/module-registry.json`` (``None`` when not shipped: callers fail closed)."""
    path = repo_path(REGISTRY_PATH)
    if not path.exists():
        return None
    doc = json.loads(path.read_text(encoding="utf-8"))
    return doc if isinstance(doc, dict) and isinstance(doc.get("modules"), list) else None
