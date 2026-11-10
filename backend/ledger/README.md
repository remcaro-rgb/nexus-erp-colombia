# Ledger (P05) — how to post, and the accounting dimensions

DEV-only edition, synthetic data. (The prompt asks for `docs/modules/ledger.md`; that path is not owned by P05, so the
guide lives here.)

## Posting from a module (in-line, same transaction — UR-14, C1-1)
```python
from kernel import services
from kernel.interfaces.common import DimensionValues, DocumentRef
from kernel.interfaces.posting import PostingLine, PostingRequest, PostingService


def issue_invoice(uow, inv):
    ...  # write the document rows first, in the same UoW
    services.get(PostingService).post(
        uow,
        PostingRequest(
            company_id=inv.company_id,
            posting_date=inv.date,
            source=DocumentRef("sales.invoice", inv.id, inv.number),
            purpose="issue",
            journal_code="SALES",
            lines=(
                PostingLine("AR", "DEBIT", inv.total, party=inv.customer_ref),
                PostingLine(
                    "SALES_REVENUE", "CREDIT", inv.net, dimensions=DimensionValues(cost_center_id=inv.cost_center_id)
                ),
                PostingLine("VAT_PAYABLE", "CREDIT", inv.vat, tax_code="IVA19"),
            ),
        ),
    )
```
* `account_role` is resolved by `AccountDetermination` (priority, then specificity: document type, journal, tax code,
  warehouse). `account:<code>` addresses an account directly (manual entries). Missing → `account_determination_missing`.
* Checks, all inside the caller's UoW (any error rolls back the whole document): period (OPEN; SOFT_CLOSED only with
  `ledger.period.update`; CLOSED/undefined → `period_closed`), postable/active account, control accounts only from their
  `control_sources` (`account_not_postable`), FX (`fx_rate_missing`), invariant 1 (`entry_unbalanced`; residual ≤ COP 1.00
  becomes an engine `ROUNDING`/`FX_ROUNDING` line), dimensions (below).
* Writes one entry per mirror book (NIIF + FISCAL), lines, `ledger_balance` rows (`INSERT … ON CONFLICT DO UPDATE`,
  sorted keys) and `ledger.entry.posted.v1`. Idempotent per `(company, source.type, source.id, purpose)`.
* `simulate()` runs the same planning code and writes nothing; `reverse()` posts a mirrored entry per book (dimension
  slots copied, archived nodes allowed), marks the originals `reversed`, emits `ledger.entry.reversed.v1`.
* DB guards: lines append-only, entries allow only `posted → reversed`, RLS on every ledger table.

## Dimensions (F-ACC-DIM)
* One table `ledger_dimension_node` for `cost_center | business_unit | aux_abierto | item_contable` (proxies
  `ledger.CostCenter`, `BusinessUnit`, `AuxAbierto`, `ItemContable` = the `kernel.dimensions` FK targets) + effective-dated
  closure table `ledger_dimension_closure`; `ledger_dimension_settings.max_depth` (default 6, 1–10).
* Composite FKs: parent `(company_id, dimension, parent_id)`; every journal-line slot `(company_id, <dim>, slot_id)` via
  constant generated columns → no cross-company or cross-dimension reference even in raw SQL; used nodes cannot be
  deleted (FK) nor change node_type/company (trigger).
* Line checks: same company (`dimension_cross_company`), DETALLE only (`dimension_node_not_postable`), ACTIVE and valid
  on the posting date (`dimension_archived`), rule matrix (`dimension_required`, `dimension_forbidden`).
* Rule matrix (`/dimension-rules`): rules `{dimension, scope_type ∈ account|account_range|document_type|concept|journal|
  stock_document, scope_ref, mode, priority}`; highest priority wins, ties → FORBIDDEN > REQUIRED > ALLOWED;
  `Account.requires_dimensions` adds REQUIRED at priority 0. `account_range` accepts `5105-5199` or `51*`; `concept`
  matches `PostingRequest.purpose`. Engine rounding lines are exempt from REQUIRED.

## API (`/api/v1/ledger/…`, permissions from `contracts/permissions/catalog.yaml`)
`dimensions/{cost-centers|business-units|aux-abierto|item-contable}` list (q, node_type, status, parent_id, valid_on, sort,
cursor) / POST · `…/tree` · `…:export` (CSV, P18 template columns) · `…/{id}` GET/PATCH(If-Match)/DELETE(If-Match) ·
`…/{id}:archive|:unarchive|:move` (`parameters.parent_id`) · `…/{id}/usage` · `dimension-rules` GET/PUT(If-Match) ·
`dimension-settings` GET/PUT · `journal-entries:simulate` · `entries` GET/POST · `entries/{id}` · `entries/{id}:reverse` ·
`periods` · `periods/{id}:soft-close|:close` · `accounts`. Writes need `Idempotency-Key`; errors are RFC 9457.

## Bootstrap
`ledger.setup.setup_company(uow, company_id)` (books, journals, roles) and `ledger.periods.ensure_year(uow, company_id, year)`.
Tests: `make test-ids IDS="T-ACC-DIM-01 … T-ACC-DIM-08"`; posting-engine tests `-k T_P05`.
