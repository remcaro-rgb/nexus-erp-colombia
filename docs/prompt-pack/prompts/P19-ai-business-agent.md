> **Prompt P19 · Wave W3 · Colombia AI-first ERP+CRM prompt pack v1.1.0 · research date 2026-11-08**
>
> **Starter context (applies to every agent; read before anything else).** You are one of several coding agents building, in parallel, a SaaS multi-tenant,
> multi-company ERP+CRM for Colombia. **Paths:** this pack lives at `docs/prompt-pack/` *relative to the repository root*. Every path in the pack is resolved from the
> repository root (never from the filesystem root): `shared/…` means `docs/prompt-pack/shared/…`, `prompts/…` means `docs/prompt-pack/prompts/…`, and code paths such as
> `backend/…`, `contracts/…`, `docs/…` are repo-root relative.
> **Reading (scoped, to save context):** always read `README.md` (sections "Honest scope statement" and "How to execute"), `shared/01-constitution.md` in full, and
> `shared/02-contracts-ownership-dag.md` §1, §3, §4. Then read **your required reading:** shared/03 ADR-009, ADR-010; shared/10 §B, §D; shared/11 §AI. Open any other shared doc only when a task needs it; look things up by ID
> (R-xx, S-xx, ADR-xxx, B-xx, EG-xx, Sx). This prompt plus that reading is designed to be enough. **The constitution overrides anything in this prompt.**
> Work only in your owned paths. Anything else goes through a CCR file in `contracts/change-requests/`. When something is ambiguous, choose the safer, more conservative option,
> record the decision in your handoff, and keep going. Never hard-code a regulatory value, never invent a rate, code, layout, or date, and never call something done unless its tests pass.

# P19 — Model-Agnostic AI Business Agent

## Mission
An AI assistant embedded in the ERP that answers questions about business data, **executes ERP processes** with confirmation, **schedules tasks**, **creates custom reports and documents**, and
**runs simulations/what-if scenarios**, using any LLM provider through a gateway (ADR-010), strictly within the user's permissions. Requirements UR-02, UR-17.

## Owned paths
`backend/ai_agent/**`, `frontend/src/features/ai_agent/**`.

## Dependencies
G2 + P17 (semantic layer), P07 (scheduler, audit), P04 (delegated grants), P18 (imports for the AI-assisted mapping), and the public API contracts.

## Architecture
1. **Provider gateway:** an `LLMProvider` interface (chat with tool calls, streaming, JSON-schema structured output, embeddings optional) and adapters: OpenAI-compatible (covers many hosted and local servers such as vLLM/Ollama),
   Anthropic, Google Gemini, Azure OpenAI. A capability matrix per model (tool calling, context size, vision), routing policies per tenant (default model, fallback chain, cost caps per user/day, data residency
   flags: "no external providers" → local only), timeouts, retries, and a **provider conformance test suite** every adapter must pass with recorded cassettes. Keys in the secret store per tenant (bring your own key) or platform keys.
2. **Tooling = the public API.** Tools are generated from the OpenAPI contracts (a curated allow-list `ai_agent/tools/catalog.yaml`: ~150 tools grouped by module, with descriptions in Spanish and English,
   JSON schemas, a `risk` class ∈ {read, write_reversible, write_posting, destructive, external_effect}). The tool executor calls the API **in-process through the same service layer with the delegated
   principal** (user ∩ grant ∩ tool allow-list). There are no tools for raw SQL, shell, code execution, or arbitrary HTTP.
3. **Data questions:** the agent uses `reporting.query` (the semantic layer: policy-aware) and record lookups; answers cite the records/reports used (deep links) and show the query for transparency; numbers are always
   computed by the backend (never by the LLM); and the answer includes the as-of time.
4. **Process execution with human-in-the-loop:** any tool with risk ≥ write_reversible produces a **plan card** (what will be created/changed, amounts, the accounting/stock preview from `:simulate`) that the user
   must confirm in the UI (or by policy auto-approve for low-risk tools under limits the admin sets). Posting/destructive/external-effect actions **always** require explicit confirmation and respect approvals
   (P07) and SoD. Idempotency keys per plan step. Partial failure → report and stop; no silent retries of posting tools.
5. **Scheduling:** create/list/pause/cancel `ScheduledJob`s (P07) whose payload is a stored, validated plan (for example "every Monday 7:00 send AR aging > 60 days as XLSX to me"). The jobs run with the owner's
   current permissions, and a natural-language description is stored.
6. **Custom reports and documents:** create saved reports via the P17 API; generate documents via templates (P17) or a generated template draft for user review; outputs stored as attachments.
7. **Simulations/what-if:** a `scenario` workspace: copy the relevant parameters/drivers (prices, costs, FX, salary increases, SMMLV hypothetical, headcount, sales volume) into a scenario sandbox, run the
   module `:simulate` endpoints and the semantic layer with overrides, and present the deltas vs the baseline (P&L, cash, payroll cost, margin) with charts. **Scenarios never post** (C1-3) and are clearly labeled
   "hypothetical", especially when using hypothetical legal values (for example a 2027 SMMLV).
8. **Memory and context:** a per-user conversation history (retention configurable; deletable by the user; DSR-aware), page context from the UI (record ids only), a tenant knowledge base (policies,
   manuals uploaded by admins, optional pgvector RAG) with document-level permissions.
9. **Safety (OWASP LLM Top 10 2025):** treat all retrieved content (documents, emails, imported files, record notes) as untrusted data delimited in prompts; tool outputs are filtered by field masks before reaching
   the model; output handling escapes HTML/markdown links (no auto-loading images or URLs from model output); excessive agency limited by the tool allow-list + confirmations + rate limits + amount caps; system prompt
   leakage tolerated (no secrets in prompts); a jailbreak/prompt-injection eval corpus in CI; PII minimization (send only needed fields); and a provider data-processing registry shown to admins (EG-6).
10. **Observability and cost:** per-turn traces (prompt hash, model, tokens, latency, tools called, outcome), a cost per tenant/user dashboard, and eval dashboards.
11. **UI:** a side panel and full-page chat, streaming, plan cards with Confirm/Edit/Cancel, citations, charts/tables rendered by the frontend from structured outputs, voice input `[A]`, Spanish-first.

## API
`/api/v1/ai/conversations` (+ messages, streaming SSE), `/plans/{id}:confirm|:cancel`, `/scenarios` (+`:run`, `:compare`), `/tools` (the list visible to the user), `/providers` (admin), `/routing-policies`,
`/usage`, `/knowledge-base/documents`, `/evals:run` (admin).

## Required tests (T-P19-nn)
01 The provider conformance suite passes for ≥ 2 adapters (cassettes) + a live smoke test when keys exist. 02 Provider swap mid-conversation keeps working. 03 Tool calls use the delegated principal: a user without
payroll permission cannot obtain salaries via any tool or aggregate (k-anonymity). 04 Write tools never execute without confirmation (property: random tool plans). 05 Posting tools respect approvals and SoD.
06 Idempotent plan execution on retry. 07 Scheduled job created by the AI runs with the owner's current permissions and can be cancelled. 08 Simulation never writes (DB row-count diff = 0 outside the scenario tables).
09 Numbers in answers come from tool results (an eval check compares them). 10 Indirect prompt injection in an imported CSV note / a supplier invoice PDF does not trigger tools. 11 HTML/markdown output sanitized. 12 Cost cap
enforcement. 13 Conversation deletion (DSR). 14 Evaluation suite (shared/11 §AI) ≥ 90% with 2 providers. 15 Local-only routing policy never calls external providers.

## Acceptance (G3)
Tests green; `docs/modules/ai-agent.md` incl. the tool catalog, the risk classes, and the provider setup.

## Sources
OWASP Top 10 for LLM Applications 2025 (T-12), OWASP API Top 10 (T-11), Ley 1581 Art. 26 international transfers (S-24) for provider routing.


---
## Universal rules for this prompt (from the constitution, repeated so this file is self-contained)
* Money is `Decimal` with the NUMERIC precisions in C2 (amount 20,4; quantity 20,6; unit cost 24,8; FX rate 24,10; percent 9,6). Never use `float`. Every write goes through `kernel.uow.unit_of_work()`. No external I/O inside a transaction (use the outbox).
* Every read and write goes through `iam.policy` (row and field). This holds for API, export, report, AI, MCP, and webhooks.
* Every state change is an API action with an `Idempotency-Key`. The UI uses only the public API.
* Regulatory values are read through the `kernel.parameters` interface (P01) backed by the `tax.parameters` store (P06), with status and source. UNKNOWN fails closed; in production a value without a legal-review stamp blocks; DISPUTED computes with a warning snapshot (C1-9).
* Forbidden: `TODO`, `FIXME`, `assert True`, skipped tests without a registered `BLK-` blocker, fake endpoints, bare `except`, and `print` in backend code.
* Tests: unit + integration with real PostgreSQL; property-based tests for calculations; failure-injection tests for every posting path; leak tests for every new read path.

## Handoff (write `docs/handoffs/P19.md` when done)
```
# Handoff P19
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
