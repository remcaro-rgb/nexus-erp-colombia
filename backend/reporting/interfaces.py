"""Frozen reporting contract (P17a core) for P17b (standard reports), P17c (exógena / certificates), P19, P20.

``CONTRACT_VERSION`` follows semver; P17a only adds; a breaking change needs a CCR.

* **Semantic queries** — :func:`reporting.engine.run` ``(ctx, company_ids, query, QueryOptions)`` → ``ReportResult``
  dict (``columns``, ``rows``, ``totals``, ``parameters``, ``warnings``, ``synthetic``, ``truncated``); the query is
  ``{entity, measures[], dimensions[], filters[{field, op, value}], sort[], limit, time_grain, compare_to, as_of}``.
  Operators: ``eq ne in not_in gt gte lt lte between is_null not_null contains in_subtree``; dimension modifiers
  ``field:root`` / ``field:parent`` (hierarchy) and ``field:<day|week|month|quarter|year>`` (dates).
  ``kernel.interfaces.ReportQueryService`` is the same engine (registered at app ready).
* **Errors** — ``semantic_query_invalid`` (400, ``reason``: unknown_entity, unknown_field, unknown_measure, operator,
  value, range, too_many, hierarchy, too_expensive …), ``field_forbidden`` (403, masked field), ``policy_denied`` (403).
* **Standard reports (P17b)** — implement ``backend/reporting/standard/urls.py`` (``urlpatterns`` for
  ``standard/<report>`` and ``standard/<report>/drill``); :mod:`reporting.urls` mounts it when present. Reports read
  ledger data only through :func:`reporting.engine.run` (entity ``ledger.journal_line``) and export with
  :func:`reporting.exports.export`. Register each report's run in :func:`record_run` (KPIs, history).
* **Exógena / certificates (P17c)** — implement ``backend/reporting/exogena/urls.py`` (``exogena/...``,
  ``certificates:generate``); mounted the same way; PDFs through ``kernel.interfaces.DocumentRenderer``.
* **Documents** — ``kernel.interfaces.DocumentRenderer`` (``documents.renderer.PdfDocumentRenderer``), template
  codes in ``documents.builtin``; source records → ``documents.service.render_source`` (stored with SHA-256 + link).
* **Events** — ``reporting.report_run.ready.v1`` (every run), ``reporting.report.scheduled_run_completed.v1``
  (scheduled runs); ``reporting.exogena_run.generated.v1`` is P17c's.
* **Permissions** — ``reporting.semantic_model.read``, ``reporting.query.read``, ``reporting.report.{read,create,
  update,delete}``, ``reporting.standard_report.read``, ``reporting.exogena_*``, ``reporting.tax_certificate.export``,
  ``documents.template.{read,create,update,delete}``, ``documents.render.export`` (catalog), plus the entity's own
  ``policy_action`` for every semantic query.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from kernel.uow import UnitOfWork

CONTRACT_VERSION = "1.0.0"

SEMANTIC_ENTITY_LEDGER = "ledger.journal_line"
SEMANTIC_ENTITY_TREASURY = "treasury.voucher"
SEMANTIC_ENTITY_PAYROLL = "payroll.payslip"


def record_run(
    uow: UnitOfWork,
    company_id: uuid.UUID,
    report_key: str,
    query: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    trigger: str = "api",
) -> uuid.UUID:
    """Store an immutable run of a non-saved report (standard reports, exógena) and emit the ready event."""
    from reporting.models import ReportRun

    row = ReportRun.objects.create(
        tenant_id=uow.ctx.tenant_id,
        company_id=company_id,
        report_key=report_key,
        state=ReportRun.State.READY,
        trigger=trigger,
        principal_id=uow.ctx.principal_id,
        query=dict(query),
        result={k: v for k, v in result.items() if k != "cached"},
        row_count=len(result.get("rows") or []),
        ledger_cutoff_seq=int(result.get("ledger_cutoff_seq") or 0),
    )
    uow.emit(
        "reporting.report_run.ready.v1",
        {"run_id": str(row.id), "report": report_key, "ledger_cutoff_seq": row.ledger_cutoff_seq},
        company_id=company_id,
    )
    return row.id
