"""Repository paths and cached loaders (no services, no network)."""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = ROOT / "contracts"
OPENAPI_JSON = CONTRACTS / "openapi" / "openapi.json"
CATALOG_YAML = CONTRACTS / "permissions" / "catalog.yaml"
REPORTS = ROOT / "reports"
GATES = ROOT / "docs" / "gates"
TRACEABILITY_CSV = ROOT / "docs" / "traceability.csv"
SHARED_08 = ROOT / "docs" / "prompt-pack" / "shared" / "08-traceability-matrix.md"


def _yaml_load(path: Path) -> Any:
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    with path.open(encoding="utf-8") as fh:
        return yaml.load(fh, Loader=loader)  # noqa: S506 - SafeLoader / CSafeLoader only


@functools.cache
def openapi_bundle() -> dict[str, Any]:
    with OPENAPI_JSON.open(encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data


@functools.cache
def permission_catalog() -> dict[str, Any]:
    data: dict[str, Any] = _yaml_load(CATALOG_YAML)
    return data
