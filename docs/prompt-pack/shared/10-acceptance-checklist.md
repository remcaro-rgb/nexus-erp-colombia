# Acceptance Checklist (Release Candidate, gate G4)

P22 and P23 produce `docs/gates/G4-evidence/` with machine-readable results. Each item: ☐ → ✅ with an evidence link. Any ❌ blocks G4.

## A. Build and operations
- [ ] `docker compose -f docker-compose.yml -f docker-compose.prod.yml up` from a clean clone reaches healthy on all services in < 10 min on 4 vCPU/16 GB.
- [ ] `make seed-demo` loads the demo tenant with 2 companies and scenarios S1–S12 green.
- [ ] Backup and point-in-time restore drill completed; restored DB passes invariants 1–9; measured RPO/RTO recorded (targets ADR-014).
- [ ] Zero-downtime migration rehearsal (expand/contract) for one schema change.
- [ ] OpenTelemetry traces span API → service → DB → outbox → worker → external adapter, with the correlation id shown in the UI error toasts.
- [ ] SBOM (CycloneDX) generated; dependency licenses compatible (no AGPL in the core unless approved); container images scanned with no critical vulns unaddressed.

## B. Functional
- [ ] Every module's UI flows from its prompt pass Playwright E2E (happy path + 3 negative paths each).
- [ ] Every money endpoint is proven to post in-line (T-P22-FIN-01), and every stock endpoint updates stock in-line (T-P22-STK-01).
- [ ] Invariants 1–12 (shared/07) pass property-based tests (≥ 500 generated cases each) and the nightly reconciliation job.
- [ ] Trial balance, balance sheet, P&L, cash flow (indirect), AR/AP aging, inventory valuation, and the fixed-asset register reconcile to each other on the S1–S12 dataset.
- [ ] Bulk import of 1,000,000 journal lines and 200,000 items completes within the documented time with resumability; export obeys field masks.
- [ ] AI agent: 40-task evaluation suite (shared/11 §AI) ≥ 90% task success with ≥ 2 different providers; 100% of write actions required confirmation; 0 policy leaks.
- [ ] MCP: conformance test against the pinned SDK passes; every tool works through a real OAuth flow with scopes; destructive tools require confirmation and support idempotency.
- [ ] Onboarding: from the S11 spreadsheet pack to first invoice in < 60 min of guided steps, with opening balances balanced and reconciled.

- [ ] POS: with the server unreachable, no sale/return/payment/number/fiscal receipt can be finalized; drafts are watermarked unposted; reconnect checkout commits atomically (S4). `pos_offline_fiscal_selling` is off and not used by any test.

## C. Colombian compliance (internal evidence; external gates separate)
- [ ] UBL XML for FE, NC, ND, DEE POS, DS, nota de ajuste DS, nómina, and nómina ajuste validates against the XSDs and DIAN toolbox examples of the pinned annex version.
- [ ] CUFE/CUDE/CUNE computed values match the annex worked examples (golden tests).
- [ ] Contingency flows (DIAN down; issuer down) produce correct documents and deadline alerts (48 h).
- [ ] Payroll golden cases (shared/11 §Payroll) match the expected values computed from the parameter fixtures, including boundary dates for Ley 2466 surcharges/night window and Ley 2101 hours.
- [ ] Every parameter used in production has `status` VERIFIED-* or DISPUTED plus a legal-review stamp, or the run blocks (a test proves fail-closed).
- [ ] Data protection: consent capture, DSR workflow with SLA timers, data inventory export, biometric opt-in with an alternative, subprocessor registry.
- [ ] Retention: posted documents cannot be deleted; retention policies ≥ 10 years configured; legal hold.

## D. Security
- [ ] ASVS 5.0 L2 checklist completed (L3 items for auth, payments, payroll) with evidence per requirement.
- [ ] OWASP API Top 10 2023 tests: BOLA/BFLA suites over all endpoints (auto-generated from OpenAPI with 3 principals per tenant across 2 tenants).
- [ ] OWASP LLM Top 10 2025: prompt-injection corpus (≥ 200 cases incl. indirect injection via document/import content), excessive-agency tests, output handling.
- [ ] SAST, dependency, secret scanning, DAST (ZAP baseline) are clean or have documented risk acceptance.
- [ ] MFA enforced for admin/finance/payroll roles; session and token lifetimes per ADR-004; break-glass tested.

## E. UX / accessibility
- [ ] axe-core: 0 serious/critical violations on all routes; manual keyboard-only run of the top 20 flows; screen-reader smoke test (NVDA + Chrome).
- [ ] Performance budgets: LCP < 2.5 s, INP < 200 ms on the reference profile for the top 10 screens; POS add-to-cart < 100 ms local.
- [ ] es-CO localization complete; no hard-coded strings (lint).

## F. Honesty
- [ ] `docs/STATUS.md` lists every [A] item with its flag state, every external gate with its status, and every parameter not VERIFIED. The RC must not be called "production-ready" or "compliant" while EG items are open.
