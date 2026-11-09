"""Test-only portal resource: a party-owned document table and its portal views.

Imported only by tests (never by the app), so no migration exists for it; the table is
created by the test fixture. It shows how P10/P11/P13 build portal endpoints on
:class:`iam.external.portal.PortalCollectionView` and lets the IDOR suite
(T-PORTAL-CORE-02) exercise a real party-owned resource.
"""

from __future__ import annotations

from typing import Any

from django.db import models

from iam.external.portal import PortalCollectionView
from kernel import ids
from kernel.fields import AmountField

TABLE = "p04probe_portal_doc"
CREATE_SQL = (
    f"CREATE TABLE {TABLE} (id uuid PRIMARY KEY, tenant_id uuid NOT NULL, company_id uuid NOT NULL, "
    "party_id uuid NULL, employee_id uuid NULL, number varchar(40) NOT NULL, amount numeric(20,4) NOT NULL, "
    "internal_cost numeric(20,4) NOT NULL)"
)


class PortalProbeDoc(models.Model):
    id = models.UUIDField(primary_key=True, default=ids.new_id)
    tenant_id = models.UUIDField()
    company_id = models.UUIDField()
    party_id = models.UUIDField(null=True)
    employee_id = models.UUIDField(null=True)
    number = models.CharField(max_length=40)
    amount = AmountField()
    internal_cost = AmountField()  # never exposed to portals

    class Meta:
        app_label = "iam"
        managed = False
        db_table = TABLE


def _doc(d: PortalProbeDoc) -> dict[str, Any]:
    return {
        "id": d.id,
        "number": d.number,
        "amount": str(d.amount),
        "internal_cost": str(d.internal_cost),
        "party_id": d.party_id,
        "company_id": d.company_id,
    }


class ProbeSupplierDocs(PortalCollectionView):
    model = PortalProbeDoc
    portal_kind = "supplier"
    permissions = {"GET": "portal.supplier_invoice.read", "POST": "portal.supplier_invoice.read"}
    portal_fields = ("id", "number", "amount", "party_id")
    search_fields = ("number",)

    def to_dict(self, obj: Any) -> dict[str, Any]:
        return _doc(obj)

    def post(self, request: Any, pk: str | None = None) -> Any:
        """Echo endpoint: proves that body-supplied scope ids are refused before any handler code."""
        from rest_framework.response import Response

        return Response({"accepted": True})


class ProbeCustomerDocs(ProbeSupplierDocs):
    portal_kind = "customer"
    permissions = {"GET": "portal.customer_invoice.read"}


SCOPED_LINE_TABLE = "p04probe_scoped_line"
SCOPED_LINE_SQL = (
    f"CREATE TABLE {SCOPED_LINE_TABLE} (id uuid PRIMARY KEY, tenant_id uuid NOT NULL, company_id uuid NOT NULL, "
    "cost_center_id uuid NULL, description varchar(200) NOT NULL, amount numeric(20,4) NOT NULL)"
)


class ScopedLine(models.Model):
    """Stand-in for a ledger line carrying the ``cost_center_id`` dimension slot."""

    id = models.UUIDField(primary_key=True, default=ids.new_id)
    tenant_id = models.UUIDField()
    company_id = models.UUIDField()
    cost_center_id = models.UUIDField(null=True)
    description = models.CharField(max_length=200)
    amount = AmountField()

    class Meta:
        app_label = "iam"
        managed = False
        db_table = SCOPED_LINE_TABLE
