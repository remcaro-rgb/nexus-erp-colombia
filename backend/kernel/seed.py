"""Demo tenant loader skeleton (P01 loader; P21 owns the data file and loaders).

``backend/kernel/fixtures/demo_tenant.yaml`` has top-level sections; each
section is handled by a loader registered with :func:`register_loader`
(``register_loader("parties", fn)``). Loaders run in order, inside one unit of
work, as the seeding service principal. Synthetic data only (D-ALL 4).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import yaml

from kernel.errors import ValidationFailed
from kernel.tenancy import Channel, PrincipalType, TenantContext
from kernel.uow import UnitOfWork, run_in_unit_of_work

DEFAULT_PATH = Path(__file__).resolve().parent / "fixtures" / "demo_tenant.yaml"

Loader = Callable[[UnitOfWork, Any], int]

_loaders: dict[str, tuple[int, Loader]] = {}


def register_loader(section: str, loader: Loader, order: int = 100) -> None:
    _loaders[section] = (order, loader)


def unregister_loader(section: str) -> None:
    _loaders.pop(section, None)


def read(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValidationFailed("demo tenant file must be a mapping")
    if data.get("synthetic") is not True:
        raise ValidationFailed("demo tenant file must declare 'synthetic: true'")
    for key in ("tenant_id", "seed_principal_id"):
        if key not in data:
            raise ValidationFailed(f"demo tenant file lacks {key!r}")
    return data


def load(path: Path = DEFAULT_PATH) -> dict[str, int]:
    """Run every registered loader whose section is present; returns row counts."""
    data = read(path)
    companies = frozenset(uuid.UUID(str(c)) for c in data.get("company_ids", []))
    ctx = TenantContext(
        tenant_id=uuid.UUID(str(data["tenant_id"])),
        company_ids=companies,
        principal_id=uuid.UUID(str(data["seed_principal_id"])),
        principal_type=PrincipalType.SERVICE,
        channel=Channel.IMPORT,
    )
    unknown = sorted(
        set(data) - set(_loaders) - {"synthetic", "tenant_id", "seed_principal_id", "company_ids", "version"}
    )

    def run(uow: UnitOfWork) -> dict[str, int]:
        counts: dict[str, int] = {}
        for section, (_order, loader) in sorted(_loaders.items(), key=lambda kv: (kv[1][0], kv[0])):
            if section in data:
                counts[section] = loader(uow, data[section])
        for section in unknown:
            counts[section] = -1  # present in the file, no loader registered yet
        return counts

    # No idempotency key → never a replay, always run()'s result.
    return cast(dict[str, int], run_in_unit_of_work(run, ctx))
