"""T-P18-01/02/03: dry-run creates zero rows; a worker killed mid-batch resumes without duplicates; each batch
is atomic; idempotent by batch hash (a changing batch is never applied twice)."""

from __future__ import annotations

import pytest

from dataio import service
from dataio.errors import ImportDuplicateBatch
from dataio.models import ImportBatch, ImportStatus
from dataio.testing import helpers as h
from ledger.models import DimensionNode

pytestmark = pytest.mark.django_db


def _rows(n: int) -> bytes:
    return h.csv_bytes(
        [
            {
                "dimension": "aux_abierto",
                "code": f"P-{i:03d}",
                "description": f"Proyecto {i} (SINTÉTICO)",
                "node_type": "DETALLE",
            }
            for i in range(n)
        ]
    )


def _count(w) -> int:
    with w.scope():
        return DimensionNode.objects.filter(company_id=w.companies["ALFA"], dimension="aux_abierto").count()


class Crash(Exception):
    pass


def test_resume_after_crash_mid_batch_no_duplicates(w, monkeypatch):
    ctx, alfa = w.ctx(), w.companies["ALFA"]
    job = h.start(ctx, alfa, "dimensions", _rows(10), batch_size=4)
    dry = service.dry_run(ctx, alfa, job.id)
    assert dry.import_status == ImportStatus.VALIDATED and _count(w) == 0

    def boom(batch_no: int) -> None:
        if batch_no == 2:
            raise Crash("worker killed")

    monkeypatch.setattr(service, "after_batch_hook", boom)
    with pytest.raises(Crash):
        service.commit(ctx, alfa, job.id)
    assert _count(w) == 4  # batch 1 committed, batch 2 rolled back atomically
    with w.scope():
        assert list(ImportBatch.objects.filter(job_id=job.id).values_list("batch_no", flat=True)) == [1]
    monkeypatch.setattr(service, "after_batch_hook", None)
    done = service.commit(ctx, alfa, job.id)  # resume
    assert done.import_status == ImportStatus.COMMITTED and done.batches_done == done.batches_total == 3
    assert _count(w) == 10 and done.counts["created"] == 10 and done.applied_rows == 10
    again = service.commit(ctx, alfa, job.id)  # replay of a committed job is a no-op
    assert again.id == done.id and _count(w) == 10


def test_changing_batch_is_not_applied_twice(w):
    from ledger.dimensions import service as dims

    ctx, alfa = w.ctx(), w.companies["ALFA"]
    data = _rows(2)
    h.commit(ctx, alfa, "dimensions", data)
    with w.scope():
        node = DimensionNode.objects.get(company_id=alfa, dimension="aux_abierto", code="P-000")
    w.run(lambda u: dims.update_node(u, node, {"description": "editado a mano"}, node.etag))
    replay = h.start(ctx, alfa, "dimensions", data)
    with pytest.raises(ImportDuplicateBatch):
        service.commit(ctx, alfa, replay.id)
    with w.scope():
        assert DimensionNode.objects.get(id=node.id).description == "editado a mano"


def test_report_has_row_results_and_errors(w):
    ctx, alfa = w.ctx(), w.companies["ALFA"]
    data = h.csv_bytes(
        [
            {"dimension": "aux_abierto", "code": "OK", "description": "ok", "node_type": "DETALLE"},
            {"dimension": "aux_abierto", "code": "MAL", "description": "", "node_type": "DETALLE"},
        ]
    )
    job = h.validate(ctx, alfa, "dimensions", data)
    headers, rows = service.report_rows(job)
    assert headers[-2:] == ["_resultado", "_error"]
    assert rows[1][-2] == "error" and "[required] description" in rows[1][-1]
    assert rows[0][-2] == "omitido"
