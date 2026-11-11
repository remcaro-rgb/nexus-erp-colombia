"""P22c — T-ACC-BRANCH-06 feature e2e (gate G3): Administración › Empresas › Sucursales.

World: the G2 gate world (company ALFA with head office BOG created by the kit, a P09 warehouse referencing it) and
the kit's ``admin`` user (iam.*.*). Spec: ``tests/e2e/features/T-ACC-BRANCH-06.branches.spec.ts``.
"""

from __future__ import annotations

from typing import Any

import pytest

from qa_harness import e2e

pytestmark = pytest.mark.django_db(transaction=True)


def world_branches(w: Any) -> dict[str, Any]:
    wh = w.warehouse("BOD-BOG")
    return {
        "user": "admin",
        "tokens": e2e.tokens(w, "admin"),
        "data": {
            "company_id": str(w.company()),
            "branch_id": str(w._branches[("ALFA", "BOG")]),
            "warehouse_code": wh.code,
        },
    }


def test_T_ACC_BRANCH_06_ui_sucursales_list_create_edit_archive_with_dependencies() -> None:
    e2e.run_feature("T-ACC-BRANCH-06", world_branches, known_gaps=("DEF-P22-011",))
