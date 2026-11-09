"""Defects filed by P22 (prompt P22 "Mission"): failing tests stay in the P22 suites marked
``xfail(strict=True, reason="DEF-<id>")`` until the owner fixes the module; a strict xfail that starts
passing fails the run, so the marker must then be removed. G-gates fail while any strict xfail remains
(``open_defects()`` lists them for the gate evidence).

Each defect has a change-request file ``contracts/change-requests/P22a-<n>.md`` for its owner.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Defect:
    id: str
    owner: str
    summary: str
    change_request: str


DEFECTS: dict[str, Defect] = {
    d.id: d
    for d in (
        Defect(
            "DEF-P22-001",
            "P02",
            "implemented kernel routes /api/health/live, /api/health/ready, /api/v1/system/environment, "
            "/api/v1/ui/context, /api/v1/schema are missing from the frozen OpenAPI (D-P02, UR-22)",
            "contracts/change-requests/P22a-2.md",
        ),
        Defect(
            "DEF-P22-002",
            "P04",
            "anonymous API requests answer 403 without WWW-Authenticate; the contract declares 401 "
            "Unauthorized (problem+json) for every protected operation",
            "contracts/change-requests/P22a-3.md",
        ),
        Defect(
            "DEF-P22-003",
            "P01",
            "GET /api/v1/schema answers 200 to anonymous callers (drf-spectacular SERVE_PERMISSIONS defaults "
            "to AllowAny); only health/live, health/ready and system/environment may be anonymous (D-ALL 1, D-P01)",
            "contracts/change-requests/P22a-4.md",
        ),
    )
}


def reason(defect_id: str) -> str:
    d = DEFECTS[defect_id]
    return f"{d.id} (owner {d.owner}): {d.summary}"


def xfail(defect_id: str):  # -> pytest.MarkDecorator
    import pytest

    return pytest.mark.xfail(strict=True, reason=reason(defect_id))


def open_defects() -> list[Defect]:
    return sorted(DEFECTS.values(), key=lambda d: d.id)
