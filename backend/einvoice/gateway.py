"""``kernel.interfaces.fiscal`` implementations backed by the gateway (registered at app ready).

``FiscalGateway.submit`` (worker only, never inside a UoW) takes the canonical model as JSON bytes
(``FiscalDocumentModel.to_dict``), registers it idempotently by ``document`` and transmits it; ``poll``
re-reads the status. ``FiscalDocuments`` is the read side for purchasing/portals.
"""

from __future__ import annotations

import json
import uuid
from typing import cast

from einvoice import lifecycle, service
from einvoice.errors import of_type
from einvoice.model import FiscalDocumentModel
from einvoice.models import FiscalDocument
from kernel import tenancy
from kernel.errors import ValidationFailed
from kernel.interfaces.common import DocumentRef
from kernel.interfaces.fiscal import FiscalStatus, FiscalStatusInfo
from kernel.uow import run_in_unit_of_work


def _info(document: DocumentRef, doc: FiscalDocument | None) -> FiscalStatusInfo:
    if doc is None:
        return FiscalStatusInfo(document, "DRAFT", None, simulated=True)
    status = cast(FiscalStatus, lifecycle.KERNEL_STATUS[lifecycle.FISCAL_STATUS[doc.status]])
    message = "SIMULADO — NO CERTIFICADO" if doc.simulated else ""
    return FiscalStatusInfo(document, status, doc.unique_code, simulated=doc.simulated, message=message)


def _find(company_id: uuid.UUID, document: DocumentRef) -> FiscalDocument | None:
    return (
        FiscalDocument.objects.filter(company_id=company_id, source_type=document.type, source_id=document.id)
        .order_by("created_at")
        .first()
    )


class EinvoiceFiscalGateway:
    mode = "FIXTURE_NOT_CERTIFIED"

    def submit(self, company_id: uuid.UUID, document: DocumentRef, payload: bytes) -> FiscalStatusInfo:
        ctx = tenancy.require_current()
        try:
            model = FiscalDocumentModel.from_dict(json.loads(payload.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValidationFailed("payload must be the JSON of a FiscalDocumentModel") from None
        doc = run_in_unit_of_work(lambda u: service.register_document(u, company_id, document, model), ctx)
        doc = of_type(doc, FiscalDocument)
        return _info(document, service.transmit(ctx, doc.id))

    def poll(self, company_id: uuid.UUID, document: DocumentRef) -> FiscalStatusInfo:
        return EinvoiceFiscalDocuments().status(company_id, document)


class EinvoiceFiscalDocuments:
    def status(self, company_id: uuid.UUID, document: DocumentRef) -> FiscalStatusInfo:
        ctx = tenancy.require_current()
        with tenancy.scope(ctx):
            return _info(document, _find(company_id, document))

    def history(self, company_id: uuid.UUID, document: DocumentRef) -> list[FiscalStatusInfo]:
        ctx = tenancy.require_current()
        with tenancy.scope(ctx):
            doc = _find(company_id, document)
            if doc is None:
                return []
            out = []
            for t in doc.transitions.order_by("at", "id"):
                status = cast(FiscalStatus, lifecycle.KERNEL_STATUS[lifecycle.FISCAL_STATUS[t.to_state]])
                out.append(
                    FiscalStatusInfo(
                        document,
                        status,
                        doc.unique_code,
                        simulated=doc.simulated,
                        message=t.reason,
                    )
                )
            return out
