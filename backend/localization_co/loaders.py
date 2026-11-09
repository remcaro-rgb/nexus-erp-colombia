"""Idempotent loaders for localization reference data (used by commands and the demo seed)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import yaml

from localization_co.models import CodeListVersion, Department, Municipality

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a mapping")
    return data


def load_divipola(data: dict[str, Any], source: str = "") -> tuple[int, int]:
    """Upsert departments and municipalities; returns (departments, municipalities)."""
    src = source or str(data.get("source", ""))
    deps = 0
    for row in data.get("departments", []):
        Department.objects.update_or_create(code=str(row["code"]).zfill(2), defaults={"name": row["name"]})
        deps += 1
    muns = 0
    for row in data.get("municipalities", []):
        code = str(row["code"]).zfill(5)
        dep_code = str(row.get("department") or code[:2]).zfill(2)
        if code[:2] != dep_code:
            raise ValueError(f"municipality {code} does not belong to department {dep_code}")
        Municipality.objects.update_or_create(
            code=code, defaults={"name": row["name"], "department_id": dep_code, "source": src[:120]}
        )
        muns += 1
    return deps, muns


def load_code_lists(data: dict[str, Any]) -> int:
    count = 0
    for row in data.get("lists", []):
        CodeListVersion.objects.update_or_create(
            list_code=row["list_code"],
            version=str(row["version"]),
            defaults={
                "annex_version": str(row.get("annex_version", "")),
                "entries": list(row.get("entries", [])),
                "synthetic": bool(row.get("synthetic", True)),
                "verified": bool(row.get("verified", False)),
                "source_ref": str(row.get("source_ref", ""))[:120],
                "valid_from": dt.date.fromisoformat(str(row["valid_from"])),
                "valid_to": dt.date.fromisoformat(str(row["valid_to"])) if row.get("valid_to") else None,
            },
        )
        count += 1
    return count


def load_defaults() -> dict[str, int]:
    deps, muns = load_divipola(load_yaml(FIXTURES / "divipola_sample.yaml"))
    lists = load_code_lists(load_yaml(FIXTURES / "code_lists.yaml"))
    return {"departments": deps, "municipalities": muns, "code_lists": lists}
