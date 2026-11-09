> **Prompt P15 · Wave W2 · Colombia AI-first ERP+CRM prompt pack v1.1.0 · research date 2026-11-08**
>
> **Starter context (applies to every agent; read before anything else).** You are one of several coding agents building, in parallel, a SaaS multi-tenant,
> multi-company ERP+CRM for Colombia. **Paths:** this pack lives at `docs/prompt-pack/` *relative to the repository root*. Every path in the pack is resolved from the
> repository root (never from the filesystem root): `shared/…` means `docs/prompt-pack/shared/…`, `prompts/…` means `docs/prompt-pack/prompts/…`, and code paths such as
> `backend/…`, `contracts/…`, `docs/…` are repo-root relative.
> **Reading (scoped, to save context):** always read `README.md` (sections "Honest scope statement" and "How to execute"), `shared/01-constitution.md` in full, and
> `shared/02-contracts-ownership-dag.md` §1, §3, §4. Then read **your required reading:** shared/03 ADR-007, ADR-012; shared/04 §A; shared/05 S-01…S-06, S-44; shared/09 EG-1…EG-3, B-06, B-09, B-15. Open any other shared doc only when a task needs it; look things up by ID
> (R-xx, S-xx, ADR-xxx, B-xx, EG-xx, Sx). This prompt plus that reading is designed to be enough. **The constitution overrides anything in this prompt.**
> Work only in your owned paths. Anything else goes through a CCR file in `contracts/change-requests/`. When something is ambiguous, choose the safer, more conservative option,
> record the decision in your handoff, and keep going. Never hard-code a regulatory value, never invent a rate, code, layout, or date, and never call something done unless its tests pass.

# P15 — DIAN Electronic Documents Gateway (FE, NC/ND, DEE POS, DS, Nómina, RADIAN)

## Mission
Generate, sign, number-check, and transmit all Colombian electronic fiscal documents asynchronously and reliably, with contingency handling, full evidence retention, and a sandbox
simulator, behind the `FiscalGateway` port (ADR-007). Production transmission stays **disabled** until EG-1/EG-2/EG-3 are recorded.

## Owned paths
`backend/einvoice/**`, `frontend/src/features/einvoice/**`, `specs/fiscal-hash/**` (CUFE/CUDE/CUNE spec + test vectors). All [P] hashing is server-side.

## Dependencies
G1 (P06 code lists/parameters, P07 scheduler/attachments). Consumers P10, P11, P12, P13 call it through events and the interface (publish the fake on day 1: it returns deterministic accepted/rejected
responses per a fixture switch).

## Build-time verification (mandatory first task)
Open the DIAN e-invoicing micrositio (S-31) and the Res. 227/2025 compiled page (S-02). Identify the **current annex versions** (FE v1.9 as of research date; nómina; DS; DEE) and download the
official technical annexes, XSDs, code lists, and the toolbox examples. Record them in `docs/compliance/annex-versions.md` with URLs, access dates, and file hashes. If anything differs from
this pack (B-06), the official annex wins; file a BLK-CCR. Verified on 2026-11-08: Res. 227/2025 art. 1.5.1.10.2 adopts FE annex **v1.9** and DEE annex **v1.0** (mandatory from 1-May-2024);
Res. 000219/2025 is about the RUB (beneficial owners), not e-invoicing, so ignore it. RADIAN event types and the 3-business-day tacit acceptance are verified (R-FE-10); the event **codes** come from the annex (B-09).

## Components
1. **Document builders** (versioned by annex version, `valid_from`): UBL 2.1 Invoice (FE: types 01 venta, 02 exportación, 03 contingencia facturador, 04 contingencia DIAN: verify the current codes),
   CreditNote, DebitNote, DEE POS, DocumentoSoporte + nota de ajuste, NominaIndividual + NominaIndividualDeAjuste, ApplicationResponse (RADIAN events), AttachedDocument (delivery container).
   Builders are pure functions from a canonical internal model (`FiscalDocumentModel`) to XML; golden-file tests per type.
2. **Hashes:** CUFE (SHA-384 over the concatenation defined in the annex, with the technical key), CUDE (with the software PIN), CUNE, and CUDS, implemented from the annex text with the annex worked examples as
   test vectors; a language-neutral spec (`specs/fiscal-hash/`) with JSON vectors. Only the server (Python) computes CUFE/CUDE in `[P]`; a TS port exists only if the `[A]` offline fiscal POS flag (ADR-015) is built.
3. **Signing:** XAdES-EPES enveloped signature per the DIAN signature policy referenced in the annex; certificate from the vault (ADR-012), decrypted only in the worker; certificate expiry
   monitoring (30/15/7 days alerts). Library choice: a maintained XML-DSig library (e.g., `signxml` or `xmlsec` bindings) with an XAdES layer you implement and test against the DIAN validator in habilitación.
4. **QR and representation data:** QR content per annex (URL with CUFE), the data needed by P17's PDF templates.
5. **Transmission adapters:** `ProviderAdapter` (generic HTTP connector configurable per PT, with mapping hooks; ship one reference implementation plus a contract test suite any PT adapter must pass),
   `DirectDianAdapter` `[A]` (SOAP web services per annex: SendBillSync/SendTestSetAsync/GetStatus/GetStatusZip/GetNumberingRange etc. — **verify the method names in the current annex**), `SandboxSimulator`
   (deterministic responses, injectable failures; refuses to run when `FISCAL_MODE=production`).
6. **Lifecycle state machine** per fiscal document: `pending_build → built → signed → submitted → accepted | accepted_with_notices | rejected | error_retryable → ... ; contingency_issued → transmitted_late`;
   all transitions logged with the request/response XML stored (encrypted, retention ≥ 10 years, legal hold). Retries with backoff for retryable errors; **never re-issue a new number automatically**.
7. **Contingency (R-FE-05):** detection (DIAN/PT unavailable → automatic switch with an admin alert; issuer-side outage → manual declaration), contingency numbering/type codes, a 48 h deadline tracker from
   service restoration, dashboards, and late-transmission evidence.
8. **Delivery to the buyer:** email with XML + PDF in a ZIP inside an AttachedDocument per annex (via P07 notifications), a delivery log, and bounce handling.
9. **Numbering ranges:** sync from DIAN/PT (GetNumberingRange or a manual load with the resolution PDF), validate the technical key, exhaustion alerts (kernel.sequences).
10. **RADIAN:** receive and emit events (acuse, recibo del bien, aceptación expresa, reclamo; tacit acceptance tracking), event codes from the annex (B-09); endorsement/factoring `[A]` behind a flag.
11. **Habilitación helper:** a guided test-set runner (generates the required number of documents of each type in habilitación mode with the TestSetId) and an evidence capture for EG-1.
12. **Monitoring UI:** a document list by status with filters, retry/override actions (role-restricted), a contingency banner, and a deadline countdowns dashboard.

## API
`/api/v1/einvoice/documents` (read + `:retry`, `:resend-email`, `/events`), `/numbering-ranges` (+`:sync`), `/contingency` (+`:declare`, `:end`), `/radian/events` (+`:emit`),
`/habilitation/test-sets` (+`:run`), `/certificates` (metadata only, upload via a secure flow), `/settings` (mode, adapter, provider config, technical key, software id: secrets write-only).

## Required tests (T-P15-nn)
01 Golden XML per document type validates against the official XSDs (fixtures stored in-repo with hashes). 02 CUFE/CUDE/CUNE match the annex examples. 03 The Python hash implementation matches all vectors (a TS port, if built for `[A]`, must match too). 04 Signature verifies with an independent verifier; a tampered XML fails. 05 State machine transitions incl. retryable vs final errors. 06 No external call inside the business transaction (an outbox-driven
test). 07 Contingency switch and the 48 h deadline computation. 08 Duplicate submission protection (the same document submitted twice → one accepted record). 09 Production mode refuses the simulator and refuses
to transmit without gate records. 10 Certificate expiry alerts. 11 Provider adapter contract suite. 12 RADIAN event XML for each event type. 13 Retention: deleting a document's evidence is impossible (immutable
storage policy test in MinIO with object lock).

## Acceptance (G2)
All document types generated and accepted by the simulator; habilitación runner ready; `docs/compliance/annex-versions.md` complete; the external gates EG-1..EG-3 documented with step-by-step instructions.

## Sources
S-01 (Res 165/2023), S-02 (Res 227/2025: annex adoption, PT requisites incl. ISO 27001, RADIAN Ch. 4), S-44 (Res 219/2025: not applicable), S-03 (Res 202/2025), S-04 (Res 167/2021), S-05 (Res 13/2021), S-06 (Res 085/2022), S-07 (D 1154/2020), S-31 (DIAN micrositio).


---
## Universal rules for this prompt (from the constitution, repeated so this file is self-contained)
* Money is `Decimal` with the NUMERIC precisions in C2 (amount 20,4; quantity 20,6; unit cost 24,8; FX rate 24,10; percent 9,6). Never use `float`. Every write goes through `kernel.uow.unit_of_work()`. No external I/O inside a transaction (use the outbox).
* Every read and write goes through `iam.policy` (row and field). This holds for API, export, report, AI, MCP, and webhooks.
* Every state change is an API action with an `Idempotency-Key`. The UI uses only the public API.
* Regulatory values are read through the `kernel.parameters` interface (P01) backed by the `tax.parameters` store (P06), with status and source. UNKNOWN fails closed; in production a value without a legal-review stamp blocks; DISPUTED computes with a warning snapshot (C1-9).
* Forbidden: `TODO`, `FIXME`, `assert True`, skipped tests without a registered `BLK-` blocker, fake endpoints, bare `except`, and `print` in backend code.
* Tests: unit + integration with real PostgreSQL; property-based tests for calculations; failure-injection tests for every posting path; leak tests for every new read path.

## Handoff (write `docs/handoffs/P15.md` when done)
```
# Handoff P15
Status: DONE | DONE-WITH-BLOCKERS (list BLK ids) | NOT-DONE (reason)
Merged PRs / commits:
Owned paths touched:
Contracts consumed (versions) / CCRs filed (ids, status):
Interfaces provided (+ fakes location):
Tests: counts, coverage %, scenario ids passing, notable property/concurrency/failure-injection tests
Requirements covered (UR ids) and acceptance criteria met (checklist):
Parameters/sources used (ids, statuses) and anything still unverified:
Known limitations, [A] items delivered vs flagged off:
Decisions taken under ambiguity (with rationale):
How to demo (commands, URLs, users):
```
