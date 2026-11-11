"""T-ACC-DIM-11 (integration, G3): import children-before-parents OK; cycle file rejected with zero rows applied;
export → import is a no-op (DIMENSIONS-ADDENDUM §5, all four dimension masters)."""

from __future__ import annotations

import pytest

from dataio.errors import ImportValidationFailed
from dataio.models import ImportStatus
from dataio.testing import helpers as h
from ledger.models import DIMENSIONS, DimensionClosure, DimensionNode

pytestmark = pytest.mark.django_db


def _rows(dimension: str) -> list[dict[str, str]]:
    # Children first: ADM-01-A (grandchild), ADM-01 (child), ADM (root).
    return [
        {"dimension": dimension, "code": "ADM-01-A", "description": "Nómina (SINTÉTICO)", "node_type": "DETALLE", "parent_code": "ADM-01", "valid_from": "2026-01-01", "status": "ACTIVE"},
        {"dimension": dimension, "code": "ADM-01", "description": "Gerencia (SINTÉTICO)", "node_type": "AGRUPADOR", "parent_code": "ADM", "valid_from": "2026-01-01", "status": "ACTIVE"},
        {"dimension": dimension, "code": "ADM", "description": "Administración (SINTÉTICO)", "node_type": "AGRUPADOR", "parent_code": "", "valid_from": "2026-01-01", "status": "ACTIVE"},
        {"dimension": dimension, "code": "VEN", "description": "Ventas (SINTÉTICO)", "node_type": "DETALLE", "parent_code": "", "valid_from": "2026-01-01", "status": "ARCHIVED"},
    ]


def _nodes(w, company):
    with w.scope():
        return {
            (n.dimension, n.code): (n.version, n.parent_id, n.node_type, n.status, n.depth, n.path)
            for n in DimensionNode.objects.filter(company_id=company)
        }


@pytest.mark.parametrize("dimension", DIMENSIONS)
def test_T_ACC_DIM_11_children_before_parents_cycle_rejected_roundtrip_noop(w, dimension):
    ctx, alfa = w.ctx(), w.companies["ALFA"]

    job = h.commit(ctx, alfa, "dimensions", h.csv_bytes(_rows(dimension)))
    assert job.import_status == ImportStatus.COMMITTED
    assert job.counts["created"] == 4 and job.applied_rows == 4
    nodes = _nodes(w, alfa)
    assert nodes[(dimension, "ADM")][4:] == (1, "ADM")
    assert nodes[(dimension, "ADM-01")][4:] == (2, "ADM/ADM-01")
    assert nodes[(dimension, "ADM-01-A")][2:] == ("DETALLE", "ACTIVE", 3, "ADM/ADM-01/ADM-01-A")
    assert nodes[(dimension, "VEN")][3] == "ARCHIVED"
    with w.scope():
        leaf = DimensionNode.objects.get(company_id=alfa, dimension=dimension, code="ADM-01-A")
        root = DimensionNode.objects.get(company_id=alfa, dimension=dimension, code="ADM")
        assert DimensionClosure.objects.filter(descendant_id=leaf.id, ancestor_id=root.id, distance=2).exists()

    # Cycle file (X → Y → Z → X) plus valid rows: rejected atomically, zero rows applied.
    cycle = [
        {"dimension": dimension, "code": "NEW-OK", "description": "Válido", "node_type": "AGRUPADOR", "parent_code": ""},
        {"dimension": dimension, "code": "X", "description": "X", "node_type": "AGRUPADOR", "parent_code": "Z"},
        {"dimension": dimension, "code": "Y", "description": "Y", "node_type": "AGRUPADOR", "parent_code": "X"},
        {"dimension": dimension, "code": "Z", "description": "Z", "node_type": "AGRUPADOR", "parent_code": "Y"},
        {"dimension": dimension, "code": "Z-HIJO", "description": "hijo del ciclo", "node_type": "DETALLE", "parent_code": "Z"},
    ]
    before = _nodes(w, alfa)
    bad = h.start(ctx, alfa, "dimensions", h.csv_bytes(cycle))
    with pytest.raises(ImportValidationFailed) as exc:
        from dataio import service

        service.commit(ctx, alfa, bad.id)
    codes = {(e["row"], e["code"]) for e in exc.value.rows}
    assert {(3, "hierarchy_cycle"), (4, "hierarchy_cycle"), (5, "hierarchy_cycle"), (6, "hierarchy_cycle")} <= codes
    assert _nodes(w, alfa) == before  # NEW-OK was not applied either
    with w.scope():
        assert bad.__class__.objects.get(id=bad.id).import_status == ImportStatus.FAILED

    # A DETALLE used as parent and a parent of another company reject the batch too.
    h.commit(ctx, w.companies["BETA"], "dimensions", h.csv_bytes([{"dimension": dimension, "code": "SOLO-BETA", "description": "B", "node_type": "AGRUPADOR"}]))
    for parent, code in (("ADM-01-A", "validation_failed"), ("SOLO-BETA", "dimension_parent_unknown")):
        rows = [
            {"dimension": dimension, "code": "OK-2", "description": "ok", "node_type": "DETALLE", "parent_code": "ADM"},
            {"dimension": dimension, "code": "MAL", "description": "mal", "node_type": "DETALLE", "parent_code": parent},
        ]
        job = h.validate(ctx, alfa, "dimensions", h.csv_bytes(rows))
        assert job.import_status == ImportStatus.FAILED
        assert [(e["row"], e["code"]) for e in job.errors] == [(3, code)]
        assert _nodes(w, alfa) == before

    # Export → import is a no-op (every format), nothing rewritten (versions unchanged).
    for fmt in ("csv", "xlsx", "jsonl"):
        data = h.export(ctx, alfa, "dimensions", fmt)
        again = h.commit(ctx, alfa, "dimensions", data, filename=f"export.{fmt}")
        assert again.import_status == ImportStatus.COMMITTED
        assert again.counts == {"created": 0, "updated": 0, "unchanged": 4, "failed": 0}
        assert _nodes(w, alfa) == before
    # The re-imported export is the same batch: a second commit of it is refused (idempotent by batch hash).
