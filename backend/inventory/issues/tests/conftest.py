"""Fixtures for F-INV-ISSUE tests: the P09a inventory world (real PostgreSQL, IAM policy, ledger posting) plus
synthetic expense accounts for the seeded concepts, cost-center / ítem-contable nodes, the seeded concepts and the
workflow contract double (P07's real engine is proven at its own gate; D-023 SoD is enforced by the double too)."""

from __future__ import annotations

import importlib
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest

from inventory.issues import registry as issue_registry
from inventory.issues import service as issues
from inventory.issues.models import IssueConcept
from kernel import services
from kernel.interfaces.documents import DocumentRenderer
from kernel.interfaces.workflow import WorkflowService
from kernel.testing.fakes import FakeDocumentRenderer, FakeWorkflowService
from ledger.dimensions import service as dims
from ledger.models import Account, AccountDetermination

EXPENSE_ACCOUNTS = (
    ("519595", "Consumo interno (SINTÉTICO)", "INVENTORY_CONSUMPTION_EXPENSE"),
    ("729595", "Consumo directo a proyectos (SINTÉTICO)", "INVENTORY_DIRECT_CONSUMPTION"),
    ("539595", "Bajas de inventario (SINTÉTICO)", "INVENTORY_SCRAP_EXPENSE"),
    ("523595", "Muestras comerciales (SINTÉTICO)", "INVENTORY_SAMPLES_EXPENSE"),
    ("529595", "Otras salidas de inventario (SINTÉTICO)", "INVENTORY_OTHER_ISSUE_EXPENSE"),
)

# P09a core test module, imported dynamically: its fixtures are re-exported here (pytest collects module
# attributes of a conftest) and mypy does not type-check the core's excluded test tree through this import.
_base: Any = importlib.import_module("inventory.tests.conftest")
Inv = Any
# P09a fixtures re-exported for this directory (pytest collects module attributes of a conftest)
_issuer_session = _base._issuer_session
clerk = _base.clerk
inv = _base.inv
inventory_services = _base.inventory_services
issuer = _base.issuer
login = _base.login
world = _base.world


@pytest.fixture(autouse=True)
def issue_services() -> Iterator[dict[str, Any]]:
    workflow = FakeWorkflowService()
    renderer: DocumentRenderer = FakeDocumentRenderer()
    workflow_svc: WorkflowService = workflow
    stack = [services.override(WorkflowService, workflow_svc), services.override(DocumentRenderer, renderer)]
    for cm in stack:
        cm.__enter__()
    issue_registry.register_fields()
    try:
        yield {"workflow": workflow, "renderer": renderer}
    finally:
        for cm in reversed(stack):
            cm.__exit__(None, None, None)


@dataclass
class IssueWorld:
    inv: Inv
    concepts: dict[str, IssueConcept]
    cost_center: uuid.UUID
    other_cost_center: uuid.UUID
    project: uuid.UUID
    accounts: dict[str, str]  # role → account code

    def concept(self, code: str) -> IssueConcept:
        return self.concepts[code]


def _node(inv: Inv, dimension: str, code: str) -> uuid.UUID:
    data = dims.NodeInput(
        code=code, description=f"Nodo {code} (SINTÉTICO)", node_type="DETALLE", valid_from="2026-01-01"
    )
    node = inv.run(lambda u: dims.create_node(u, inv.company(), dimension, data))
    return uuid.UUID(str(node.id))


@pytest.fixture
def iw(inv: Inv) -> IssueWorld:
    ctx = inv.ctx()

    def accounts(u: Any) -> None:
        for code, name, role in EXPENSE_ACCOUNTS:
            acc = Account.objects.create(
                tenant_id=ctx.tenant_id, company_id=inv.company(), code=code, name=name, type="expense", nature="debit"
            )
            AccountDetermination.objects.create(
                tenant_id=ctx.tenant_id, company_id=inv.company(), role_code=role, account=acc
            )

    inv.run(accounts)
    seeded = inv.run(lambda u: issues.seed_concepts(u, inv.company()))
    return IssueWorld(
        inv=inv,
        concepts={c.code: c for c in seeded},
        cost_center=_node(inv, "cost_center", "CC-PROD"),
        other_cost_center=_node(inv, "cost_center", "CC-ADMIN"),
        project=_node(inv, "item_contable", "OBRA-01"),
        accounts={role: code for code, _n, role in EXPENSE_ACCOUNTS},
    )


@pytest.fixture
def supervisor(world: Any, inv: Inv) -> str:
    """Second user with the same inventory rights: the approver (the requester never approves, D-023)."""
    world.add_role("inv_sup", ["inventory.*.*", "workflow.approval.approve", "ledger.*.read"], max_cls="confidential")
    world.add_user("supervisor", ["ALFA"], subject=str(uuid.uuid4()))
    world.assign("supervisor", "inv_sup", "ALFA")
    return "supervisor"


@pytest.fixture
def seller(world: Any, inv: Inv) -> str:
    """Sales role: may read issues but not confidential cost fields (max_classification internal)."""
    world.add_role("ventas", ["inventory.issue.read", "inventory.issue.export"], max_cls="internal")
    world.add_user("vendedor", ["ALFA"], subject=str(uuid.uuid4()))
    world.assign("vendedor", "ventas", "ALFA")
    return "vendedor"
