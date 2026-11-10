"""Habilitación helper (EG-1): guided test-set runner + evidence capture.

The number of documents per type required by a DIAN test set is set by DIAN for each TestSetId
(EXTERNAL input, never invented): the runner takes it as ``required`` (``{"FE": n, "NC": n, …}``); the
default is a labelled synthetic set. Documents are generated in environment 2 with the TestSetId, sent
through the company's adapter (simulator or PT sandbox), and the evidence (numbers, CUFE/CUDE, adapter
answers, timestamps) is stored on the test set for the operator's EG-1 record.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import replace
from typing import Any

from einvoice import service
from einvoice.adapters.contract import sample_model
from einvoice.errors import of_type
from einvoice.model import BillingReference, FiscalDocumentModel
from einvoice.models import FiscalDocument, HabilitationTestSet, NumberingRange
from kernel import clock, ids, tenancy
from kernel.errors import ValidationFailed
from kernel.interfaces.common import DocumentRef
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork, run_in_unit_of_work

SYNTHETIC_REQUIRED = {"FE": 2, "NC": 1, "ND": 1}
SUPPORTED = ("FE", "NC", "ND")
TEST_PREFIX = "SETT"


def _ensure_test_range(ctx: TenantContext, company_id: uuid.UUID) -> NumberingRange:
    def fn(uow: Any) -> NumberingRange:
        rng = NumberingRange.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, code="HAB-SETT").first()
        if rng is None:
            rng = NumberingRange.objects.create(
                tenant_id=ctx.tenant_id,
                company_id=company_id,
                code="HAB-SETT",
                family="FE",
                prefix=TEST_PREFIX,
                from_number=1,
                to_number=5_000_000,
                resolution="SINTETICO-HABILITACION",
                valid_to=dt.date(2099, 12, 31),
                synthetic=True,
                created_by=ctx.principal_id,
            )
        return rng

    out = run_in_unit_of_work(fn, ctx)
    return of_type(out, NumberingRange)


def run(ctx: TenantContext, test_set: HabilitationTestSet, required: dict[str, int] | None = None) -> dict[str, Any]:
    """Generate and transmit the test set (outside a UoW). Returns the results to store."""
    plan = dict(required or SYNTHETIC_REQUIRED)
    bad = sorted(set(plan) - set(SUPPORTED))
    if bad or any(not isinstance(v, int) or not 0 <= v <= 50 for v in plan.values()):
        raise ValidationFailed(f"required must map {', '.join(SUPPORTED)} to 0..50", field="parameters.required")
    company_id = test_set.company_id
    _ensure_test_range(ctx, company_id)
    with tenancy.scope(ctx):
        used = FiscalDocument.objects.filter(
            tenant_id=ctx.tenant_id, company_id=company_id, number__startswith=TEST_PREFIX, family="FE"
        ).count()
    rows: list[dict[str, Any]] = []
    base_invoice: FiscalDocument | None = None
    seq = used + 1
    for family in SUPPORTED:
        for _ in range(plan.get(family, 0)):
            if family == "FE":
                number = f"{TEST_PREFIX}{seq}"
                seq += 1
            else:
                number = f"{family}{TEST_PREFIX}{ids.new_id().hex[-8:]}"
            model = replace(sample_model(number), issue_date=clock.business_date(), fixture_outcome="accept")
            if family != "FE":
                if base_invoice is None:
                    raise ValidationFailed(
                        "notes need at least one invoice in the test set", field="parameters.required"
                    )
                model = replace(
                    model,
                    family=family,
                    concept_code="2" if family == "NC" else "4",
                    billing_reference=BillingReference(
                        base_invoice.number, base_invoice.unique_code, base_invoice.issue_date
                    ),
                )
            model.validate()
            source = DocumentRef("einvoice.habilitation", ids.new_id(), number)

            def register(u: UnitOfWork, m: FiscalDocumentModel = model, s: DocumentRef = source) -> FiscalDocument:
                return service.register_document(u, company_id, s, m, test_set_id=test_set.test_set_id)

            doc = of_type(run_in_unit_of_work(register, ctx), FiscalDocument)
            doc = service.transmit(ctx, doc.id)
            if family == "FE" and base_invoice is None:
                base_invoice = doc
            rows.append(
                {
                    "family": family,
                    "number": doc.number,
                    "unique_code": doc.unique_code,
                    "status": doc.status,
                    "tracking_id": doc.tracking_id,
                    "adapter": doc.adapter_key,
                    "simulated": doc.simulated,
                }
            )
    accepted = sum(1 for r in rows if r["status"] in ("accepted", "accepted_with_notices"))
    return {
        "required": plan,
        "required_source": "SYNTHETIC (DIAN sets the real counts per TestSetId)" if required is None else "operator",
        "documents": rows,
        "accepted": accepted,
        "total": len(rows),
        "passed": accepted == len(rows) and bool(rows),
        "ran_at": clock.now().isoformat(),
        "evidence_note": "Evidencia para EG-1 — en DEV el validador es el simulador (no certificado).",
    }
