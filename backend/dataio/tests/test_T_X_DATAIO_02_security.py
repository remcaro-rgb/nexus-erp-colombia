"""T-X-DATAIO-02 (security, G3): an import cannot create cross-company references or bypass approvals (documents
import as DRAFT); the same permissions as the target module apply to import and export (F-X-DATAIO Security)."""

from __future__ import annotations

from typing import Any

import pytest

from dataio import service
from dataio.errors import ImportFileRejected, ImportValidationFailed, PolicyDenied
from dataio.models import ImportStatus
from dataio.testing import helpers as h
from kernel.errors import ValidationFailed
from kernel.uow import run_in_unit_of_work

pytestmark = pytest.mark.django_db


def seed_beta_only(w) -> None:
    """Codes that exist only in BETA (same tenant): ALFA imports must never resolve them."""
    from iam.models import Branch
    from inventory import masters
    from inventory.models import Warehouse

    beta = w.companies["BETA"]

    def fn(u: Any) -> None:
        masters.create_item(u, beta, {"code": "SOLO-BETA", "name": "Ítem de BETA (SINTÉTICO)", "base_uom": "UND"})
        branch = Branch.objects.get(company_id=beta, code="BOG")
        masters.create_warehouse(u, beta, {"code": "BODEGA-BETA", "name": "Bodega BETA", "branch_id": str(branch.id)})
        assert Warehouse.objects.filter(company_id=beta, code="BODEGA-BETA").exists()

    run_in_unit_of_work(fn, w.ctx())
    h.commit(w.ctx(), beta, "dimensions", h.csv_bytes([
        {"dimension": "business_unit", "code": "UN-BETA", "description": "UN de BETA", "node_type": "DETALLE"},
        {"dimension": "cost_center", "code": "CC-BETA", "description": "CC de BETA", "node_type": "AGRUPADOR"},
    ]))


#: entity → rows of an ALFA file whose only defect is a reference to a BETA-only code (and the offending column)
CROSS: dict[str, tuple[list[dict[str, str]], str]] = {
    "dimensions": ([{"dimension": "cost_center", "code": "CC-A", "description": "hijo", "node_type": "DETALLE", "parent_code": "CC-BETA"}], "parent_code"),
    "branches": ([{"code": "MED", "name": "Medellín", "is_head_office": "false", "default_business_unit_code": "UN-BETA"}], "default_business_unit_code"),
    "presentations": ([{"item_code": "SOLO-BETA", "code": "CAJA", "name": "Caja", "uom": "caja", "factor": "12"}], "item_code"),
    "locations": ([{"warehouse_code": "BODEGA-BETA", "code": "X-01", "name": "X", "location_type": "INTERNAL"}], "warehouse_code"),
    "gtins": ([{"value": "7701234567897", "owner_type": "item", "item_code": "SOLO-BETA"}], "item_code"),
    "boms": ([{"code": "B-1", "name": "B", "item_code": "PAN", "output_qty": "1", "valid_from": "2026-01-01", "component_code": "SOLO-BETA", "quantity": "1"}], "component_code"),
    "portioning-templates": ([{"code": "P-1", "name": "P", "input_item_code": "SOLO-BETA", "output_code": "LOMO", "expected_yield_percent": "50", "is_main": "true"}], "input_item_code"),
    "price-formulas": ([{"code": "F-1", "formula_code": "PRECIO_MAYOR", "valid_from": "2026-01-01", "scope_item_code": "SOLO-BETA"}], "scope_item_code"),
    "internal-requisitions": ([{"reference": "R-1", "needed_by": "2026-11-20", "requesting_warehouse_code": "BODEGA-BETA", "item_code": "ARROZ", "quantity": "1"}], "requesting_warehouse_code"),
    "external-account-invitations": ([{"email": "x@example.invalid", "portal_kind": "supplier", "party_code": "PRV-BETA"}], "party_code"),
}


@pytest.mark.parametrize("entity", sorted(CROSS))
def test_T_X_DATAIO_02_no_cross_company_reference(w, entity):
    seed_beta_only(w)
    from sales.models import Party

    with w.scope():
        Party.objects.filter(company_id=w.companies["BETA"], code="PRV-001").update(code="PRV-BETA")
    ctx, alfa = w.ctx(), w.companies["ALFA"]
    before = h.table(h.export(ctx, alfa, entity))
    rows, column = CROSS[entity]
    job = h.start(ctx, alfa, entity, h.csv_bytes(rows))
    with pytest.raises(ImportValidationFailed) as exc:
        service.commit(ctx, alfa, job.id)
    assert [(e["row"], e.get("column")) for e in exc.value.rows] == [(2, column)], exc.value.rows
    assert h.table(h.export(ctx, alfa, entity)) == before


def test_T_X_DATAIO_02_company_never_taken_from_the_file_or_outside_the_context(w):
    ctx, alfa = w.ctx(), w.companies["ALFA"]
    for col in ("company_id", "tenant_id", "id", "Empresa"):
        data = h.csv_bytes([{col: str(w.companies["BETA"]), "dimension": "cost_center", "code": "Z", "description": "z", "node_type": "DETALLE"}])
        with pytest.raises(ImportFileRejected):
            h.start(ctx, alfa, "dimensions", data)
    only_alfa = w.user("solo_alfa", ["*.*.*"], companies=("ALFA",))
    with pytest.raises(ValidationFailed):
        h.start(only_alfa, w.companies["BETA"], "dimensions", h.csv_bytes([{"dimension": "cost_center", "code": "Z", "description": "z", "node_type": "DETALLE"}]))
    # An ALFA job is invisible from a BETA-only context (no existence oracle).
    job = h.start(ctx, alfa, "dimensions", h.csv_bytes([{"dimension": "cost_center", "code": "Z", "description": "z", "node_type": "DETALLE"}]))
    only_beta = w.user("solo_beta", ["*.*.*"], companies=("BETA",))
    with pytest.raises(ValidationFailed):
        service.commit(only_beta, alfa, job.id)


def test_T_X_DATAIO_02_documents_import_as_draft_and_never_skip_approval(w, svc):
    from purchasing.requisitions import service as reqs
    from purchasing.requisitions.models import InternalRequisition

    ctx, alfa = w.ctx(), w.companies["ALFA"]
    row = {"reference": "REQ-A", "needed_by": "2026-11-20", "requesting_warehouse_code": "PRINCIPAL", "item_code": "ARROZ", "quantity": "4"}
    for status in ("APPROVED", "SUBMITTED", "FULFILLED"):
        job = h.start(ctx, alfa, "internal-requisitions", h.csv_bytes([{**row, "status": status}]))
        with pytest.raises(ImportValidationFailed) as exc:
            service.commit(ctx, alfa, job.id)
        assert [e["code"] for e in exc.value.rows] == ["invalid_state_transition"]
    with w.scope():
        assert not InternalRequisition.objects.filter(company_id=alfa).exists()

    requester = w.user("solicitante", ["dataio.*.*", "purchasing.internal_requisition.*", "inventory.*.read"], companies=("ALFA",))
    job = h.commit(requester, alfa, "internal-requisitions", h.csv_bytes([{**row, "status": "DRAFT"}]))
    assert job.import_status == ImportStatus.COMMITTED
    with w.scope():
        req = InternalRequisition.objects.get(company_id=alfa)
    assert (req.status, req.approval_request_id, req.decided_by) == ("DRAFT", None, None)
    assert req.requester_id == requester.principal_id  # never taken from the file
    assert req.source["type"] == "dataio.import" and req.source["number"] == "REQ-A"

    # Once submitted, the document leaves the import's reach: no edit, no status change.
    run_in_unit_of_work(lambda u: reqs.submit(u, InternalRequisition.objects.get(id=req.id)), requester)
    edit = h.start(requester, alfa, "internal-requisitions", h.csv_bytes([{**row, "quantity": "40", "status": "DRAFT"}]))
    with pytest.raises(ImportValidationFailed) as exc:
        service.commit(requester, alfa, edit.id)
    assert [e["code"] for e in exc.value.rows] == ["invalid_state_transition"]
    with w.scope():
        req.refresh_from_db()
        assert req.status == "SUBMITTED" and req.decided_by is None
        assert [str(ln.quantity) for ln in req.lines.all()] == ["4.000000"]


def test_T_X_DATAIO_02_boms_never_activated_by_import(w):
    from inventory.bom import service as boms
    from inventory.bom.models import Bom

    ctx, alfa = w.ctx(), w.companies["ALFA"]
    base = {"code": "PAN-9", "name": "Pan", "item_code": "PAN", "output_qty": "10", "valid_from": "2026-01-01", "component_code": "HARINA", "quantity": "5"}
    job = h.start(ctx, alfa, "boms", h.csv_bytes([{**base, "status": "ACTIVE"}]))
    with pytest.raises(ImportValidationFailed):
        service.commit(ctx, alfa, job.id)
    h.commit(ctx, alfa, "boms", h.csv_bytes([base]))
    with w.scope():
        bom = Bom.objects.get(company_id=alfa, code="PAN-9")
        assert bom.bom_status == "DRAFT"
    run_in_unit_of_work(lambda u: boms.activate(u, Bom.objects.get(id=bom.id)), ctx)
    h.commit(ctx, alfa, "boms", h.csv_bytes([{**base, "quantity": "6", "status": "ACTIVE"}]))
    with w.scope():
        versions = list(Bom.objects.filter(company_id=alfa, code="PAN-9").order_by("version_no").values_list("version_no", "bom_status"))
    assert versions == [(1, "ACTIVE"), (2, "DRAFT")]  # the active version is untouched; the change waits as a DRAFT


def test_T_X_DATAIO_02_same_permissions_as_the_target_module(w):
    alfa = w.companies["ALFA"]
    rows = h.csv_bytes([{"code": "CONSUMO", "name": "Consumo", "account_role": "EXPENSE"}])
    nobody = w.user("sin_dataio", ["inventory.*.*"])
    with pytest.raises(PolicyDenied):
        h.start(nobody, alfa, "issue-concepts", rows)
    clerk = w.user("solo_dataio", ["dataio.*.*", "inventory.issue_concept.read"])
    job = h.start(clerk, alfa, "issue-concepts", rows)
    with pytest.raises(ImportValidationFailed) as exc:
        service.commit(clerk, alfa, job.id)
    assert [e["code"] for e in exc.value.rows] == ["policy_denied"]
    # create allowed, update denied: new rows pass, a changed existing row is refused
    w.policy.grant(clerk.principal_id, ["inventory.issue_concept.create"])
    assert h.commit(clerk, alfa, "issue-concepts", rows).counts["created"] == 1
    changed = h.start(clerk, alfa, "issue-concepts", h.csv_bytes([{"code": "CONSUMO", "name": "Otro nombre", "account_role": "EXPENSE"}]))
    with pytest.raises(ImportValidationFailed) as exc:
        service.commit(clerk, alfa, changed.id)
    assert [e["code"] for e in exc.value.rows] == ["policy_denied"]
    # export needs the target read permission and only returns rows of the job's company
    w.policy.revoke(clerk.principal_id, "inventory.issue_concept.read")
    with pytest.raises(PolicyDenied):
        h.export(clerk, alfa, "issue-concepts")
    h.commit(w.ctx(), w.companies["BETA"], "issue-concepts", h.csv_bytes([{"code": "SOLO_BETA", "name": "B", "account_role": "EXPENSE"}]))
    codes = [r["code"] for r in h.table(h.export(w.ctx(), alfa, "issue-concepts"))]
    assert codes == ["CONSUMO"]
