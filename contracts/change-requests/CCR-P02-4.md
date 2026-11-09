# CCR-P02-4 — naming decisions taken at the freeze (for P00/integrator review at G0)

Status: RECORDED (decided by P02 under DECISIONS-DEFAULTS precedence; no other task's files touched)

* Stock error code: `stock_insufficient` (FEATURES-ADDENDUM) is THE code; the P02 prompt example `insufficient_stock` is not registered (one rule, one code).
* Journal entries: P05's `/api/v1/ledger/entries` (+`:post`, `:reverse`, `:simulate`) is the resource; FEATURES-ADDENDUM's
  `POST /api/v1/ledger/journal-entries:simulate` is a stateless ad-hoc simulation (dimension validation) with the same permission `ledger.entry.read`.
* Cost centers: master in `/api/v1/ledger/dimensions/cost-centers` (F-ACC-DIM supersedes P04); `/api/v1/iam/cost-centers` stays as a read-only scope projection.
* Treasury `receipts`/`disbursements` (RC/CE) live in `/api/v1/treasury/`; `/api/v1/sales/receipts` is the AR-application view of a recibo de caja.
* MCP (P20) and the UX shell (P08) declare no REST API; MCP scopes are the `<module>.<resource>.read` permission codes, tools listed by `GET /api/v1/ai/tools`.
* Portal API has no `X-Company-Id`; the party/employee scope and company come from the external identity's token (D-033).
