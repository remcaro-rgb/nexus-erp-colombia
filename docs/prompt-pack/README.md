# Colombia AI-First Multi-Company ERP + CRM — Parallel Build Prompt Pack

Research date: **2026-11-08** (America/Bogota). Pack version: **1.1.0** (v1.1 adds a primary-source verification pass: official texts opened via the Rama Judicial SIDN, DIAN normograma, and CRA compilations; see shared/05).
Built for: Manuel Caro. Nothing in this pack has been executed; it contains no code and changes no repository.

## What this is

A set of copy-ready prompts that let one **root orchestrator agent** coordinate up to **~10 coding agents at once**
across **5 waves** to build a SaaS, multi-tenant, multi-company ERP + CRM localized for Colombia:
accounting and finance, inventory and costing, invoicing and AR, purchasing and AP, POS, HR, payroll and timekeeping, fixed assets and
depreciation, CRM, treasury, intercompany and consolidation, bulk import/export, a model-agnostic business AI agent,
an AI onboarding/migration agent, an MCP server, and granular row- and field-level security. Every business process is exposed through the API.

## Honest scope statement (read first)

* **One entry point:** paste `prompts/P00-master-orchestrator.md` into your orchestrating agent. It runs everything else.
  The pack is designed so the build *can* run end-to-end in one coordinated execution.
* **No prompt can guarantee production readiness or legal compliance.** Some steps need people and outside parties:
  DIAN habilitación test sets and a production enablement, a digital signature certificate from an accredited certification body (ONAC),
  technology-provider (PT) contracts or sandbox credentials, PILA operator access, bank file formats,
  LLM provider keys, a sign-off from a Colombian tax/labor/data-protection lawyer and a contador público, and
  penetration testing. The prompts treat these as **explicit release gates**
  (`shared/09-blockers-and-release-gates.md`). Production stays **fail-closed** until each gate has signed-off evidence.
  These gates are not an excuse for unfinished work. Everything that does not need an outside credential must be delivered complete and tested.
* Regulatory values (UVT, minimum wage, rates, surcharges, thresholds) are loaded as **dated, sourced parameters** (interface `kernel.parameters` defined by P01; store `tax.parameters` implemented and seeded by P06),
  never hardcoded. Values not yet published (for example 2027 minimum wage/UVT as of 2026-11-08) are marked UNKNOWN and block
  the affected calculations for those dates until they are loaded.

## Files

| Path | Purpose |
|---|---|
| `README.md` | This guide |
| `prompts/P00-master-orchestrator.md` | **Root entry prompt**. Run this one. |
| `prompts/P01..P23-*.md` | Self-contained worker prompts (one per agent) |
| `shared/01-constitution.md` | Non-negotiable rules every agent obeys |
| `shared/02-contracts-ownership-dag.md` | Repo layout, path ownership, DAG, waves, merge/migration protocol |
| `shared/03-architecture-adr.md` | Architecture decision records (stack, modular monolith, outbox, RLS...) |
| `shared/04-regulatory-applicability-matrix.md` | Colombia rules → modules, with effective dates, status, and legal-review flags |
| `shared/05-source-register.md` | Every source: URL, access date, type, verification status |
| `shared/06-erp-benchmark-features.md` | Leading-ERP features translated into must-have and advanced capabilities |
| `shared/07-posting-matrix-and-invariants.md` | Accounting posting matrix and money/stock invariants |
| `shared/08-traceability-matrix.md` | Requirement → prompt → test IDs |
| `shared/09-blockers-and-release-gates.md` | External blockers, fail-closed rules, gate owners |
| `shared/10-acceptance-checklist.md` | Final acceptance checklist and evidence bundle spec |
| `shared/11-seed-scenarios.md` | Deterministic synthetic end-to-end scenarios (COP) |

Distribution files (outside the pack folder, so they don't get committed): `colombia-erp-prompt-pack.zip` (this folder) and
`colombia-erp-master-prompts.md` (README + shared + prompts concatenated, for single-paste use). Both sit next to the folder.

## How to execute

1. Create an empty Git repository (for example `erp-co`). Copy this folder (`README.md`, `shared/`, `prompts/`) into it at **`docs/prompt-pack/`**, relative to the
   repository root. **All paths in this pack resolve from the repository root** (`shared/…` = `docs/prompt-pack/shared/…`). The distribution ZIP and the consolidated
   master file live **outside** this folder and must not be committed.
   Each prompt names the shared sections it needs (scoped reading). Only P00 reads all of `shared/`, progressively.
2. Give the orchestrator agent repository write access and the ability to start sub-agents, each with its own branch or worktree.
3. Paste **`prompts/P00-master-orchestrator.md`** into the orchestrator. It will:
   * Wave 0 (3 agents in parallel): P01 Foundation, P02 Contract Freeze, P03 DevOps/CI.
     → Gate G0 (contracts frozen, CI green, kernel merged)
   * Wave 1 (5 in parallel): P04 IAM/RLS/Field policy, P05 Ledger/Posting, P06 Tax & Localization, P07 Workflow/Audit/Scheduler, P08 UX shell.
     → Gate G1
   * Wave 2 (8 in parallel): P09 Inventory, P10 Sales/AR/CRM, P11 Purchasing/AP, P12 POS, P13 HR/Payroll/Time, P14 Fixed Assets,
     P15 DIAN e-documents gateway, P16 Treasury/Intercompany/Consolidation.
     → Gate G2 (cross-module financial consistency suite green)
   * Wave 3 (5 in parallel): P17 Reporting/Semantic layer/Documents, P18 Bulk Import/Export, P19 AI Business Agent, P20 MCP Server, P21 Onboarding Agent.
     → Gate G3
   * Wave 4: P22 QA/Security/E2E verification (also runs continuously from Wave 1), P23 Compliance evidence and legal-review packet.
     → Gate G4 (release candidate). Then **external gates** EG-1..EG-13 before production.
4. Do not let agents edit `shared/` or another agent's paths. Changes go through Contract Change Requests (CCR), described in shared/02.
5. Provide secrets only through the secret mechanism in ADR-012, never in prompts or the repository.

## Recommended stack (decided, see ADR-001..ADR-015)

Python 3.13 + **Django 5.2 LTS** (security support to April 2028; plan the upgrade to 6.2 LTS after its April 2027 release) + Django REST Framework
+ **PostgreSQL 18** (RLS, NUMERIC, SERIALIZABLE/row locks) + Celery workers on Redis/Valkey, fed by a **transactional outbox** in PostgreSQL
+ React + TypeScript (Vite, TanStack Query/Table/Router, Radix-based accessible components, i18n es-CO)
+ Keycloak (OIDC/OAuth 2.1, MFA) + S3-compatible object storage (MinIO in dev) + OpenTelemetry.
The architecture is a **modular monolith**: one transactional database, so double-entry accounting and stock updates commit atomically in the same transaction.
Deployment uses Docker Compose for dev, staging, and single-node production. Orchestrators such as Kubernetes are optional and not required.
The MCP server is a separate container that targets the **MCP 2026-07-28** specification (current as of the research date) and stays backward compatible with 2025-11-25.

## Scope tiers

* **Tier P (production must-have):** everything tagged `[P]` in the prompts. Release requires 100% of these.
* **Tier A (advanced, feature-flagged):** tagged `[A]`. Examples: disconnected (offline) fiscal POS selling (ADR-015, EG-13; off by default because it conflicts with in-line posting), RADIAN endorsement/factoring events, light manufacturing (BoM/assembly), standard cost, consolidation with currency translation
  and minority interest, books beyond NIIF + fiscal, demand forecasting, OFX/CAMT and per-bank file adapters, DOCX templates, voice input, Kubernetes manifests.
  If an `[A]` item is not delivered, it ships disabled and the release notes must say so. **The release notes must never claim the product is "feature complete" while any `[A]` item is missing.**
