"""P22c — pages delivered by their owners but not registered in the P08 shell descriptors (strict-xfail defects).

The feature e2e specs mount these pages with ``augment: true`` (tests/e2e/features/support/app-main.tsx). That only
fills keys the owner did not register, so the gap would be invisible there; these tests keep it visible: each
asserts the registry key of the page is registered by the module descriptor the shell auto-discovers
(``frontend/src/features/<module>/module.ts``), directly or by spreading the part's page map. Strict xfail while the
defect is open; the marker must be removed once the owner registers the page.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

FEATURES = Path(__file__).resolve().parents[2] / "frontend" / "src" / "features"

CASES = [
    # (defect, descriptor file, registry keys, part page map that may be spread instead)
    ("DEF-P22-012", "inventory/module.ts", ["movimientos/salidas", "maestros/conceptos-salida"], None),
    ("DEF-P22-013", "inventory/module.ts", ["produccion/recetas"], "bomPages"),
    ("DEF-P22-013", "inventory/module.ts", ["produccion/transformaciones", "produccion/plantillas-porcionado"], "transformationPages"),
    ("DEF-P22-014", "purchasing/module.ts", ["solicitudes/mis-solicitudes", "solicitudes/por-aprobar", "solicitudes/consolidacion", "solicitudes/abastecimiento"], "requisitionPages"),
    ("DEF-P22-015", "hr/module.ts", ["nomina/conceptos"], None),
]


def _registered(descriptor: Path, keys: list[str], spread: str | None) -> list[str]:
    if not descriptor.is_file():
        return keys
    src = descriptor.read_text(encoding="utf-8")
    if spread and re.search(rf"\.\.\.\s*{spread}\b", src):
        return []
    return [k for k in keys if f'"{k}"' not in src]


@pytest.mark.parametrize(
    ("defect", "descriptor", "keys", "spread"),
    [pytest.param(*c, marks=pytest.mark.xfail(strict=True, reason=c[0]), id=f"{c[0]}-{c[2][0]}") for c in CASES],
)
def test_p22c_delivered_page_is_registered_in_its_shell_descriptor(defect: str, descriptor: str, keys: list[str], spread: str | None) -> None:
    missing = _registered(FEATURES / descriptor, keys, spread)
    assert missing == [], f"{defect}: {descriptor} does not register {missing}"


def test_p22c_wiring_checker_sees_registered_pages() -> None:
    """Self-check of the checker on pages that ARE registered (inventory existencias, compras cotizaciones)."""
    assert _registered(FEATURES / "inventory/module.ts", ["existencias/por-ubicacion"], None) == []
    assert _registered(FEATURES / "purchasing/module.ts", ["cotizaciones/comparacion"], None) == []
    assert _registered(FEATURES / "purchasing/module.ts", ["no/such-page"], None) == ["no/such-page"]
