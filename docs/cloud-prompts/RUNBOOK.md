# RUNBOOK — Nexus ERP Colombia on Claude Code cloud sessions (edition 3.0.1-claude-cloud)

Repository: https://github.com/remcaro-rgb/nexus-erp-colombia. Peak parallel sessions: **5 workers** (+1 integrator that runs alone between waves). See PARALLELISM.md.

Every step: open a NEW session at https://claude.ai/code, select the repository, paste the whole prompt file, send. Do not answer questions; the sessions do not ask.
A worker is done when its final message starts with `RESULT` (its branch then contains `docs/run/reports/TASK.json`).
After each integrator, the authoritative next list is the integrator's final message (also in `docs/run/STATUS.md` on `main`). Rework, retries and blocked items appear there; this table is the happy path.

| Step | Wave | Sessions | Paste these files (one per session) | Done when | Then |
|---|---|---|---|---|---|
| 1 | W01 | 3 (max 5 at once) | `W01-S1-P01-foundation-kernel.md`<br>`W01-S2-P02-contract-freeze.md`<br>`W01-S3-P03-devops-ci.md` | every session printed `RESULT … DONE` or `RESULT … BLOCKED` | step 2 |
| 2 | W01 | 1 | `INTEGRATOR-after-W01.md` | final message `INTEGRATED wave 01`; G0 closed | its NEXT list |
| 3 | W02 | 6 (max 5 at once) | `W02-S1-P04-iam-rls-field-security.md`<br>`W02-S2-P05-ledger-posting-engine.md`<br>`W02-S3-P07-workflow-audit-scheduler.md`<br>`W02-S4-P08-ux-shell-design-system.md`<br>`W02-S5-P06-tax-localization-co.md`<br>`W02-S6-P22a-qa-harness.md` | every session printed `RESULT … DONE` or `RESULT … BLOCKED` | step 4 |
| 4 | W02 | 1 | `INTEGRATOR-after-W02.md` | final message `INTEGRATED wave 02`; G1 closed | its NEXT list |
| 5 | W03 | 7 (max 5 at once) | `W03-S1-P15-dian-einvoice-gateway.md`<br>`W03-S2-P09a-inventory-core.md`<br>`W03-S3-P13b-payroll-formulas.md`<br>`W03-S4-P11a-purchasing-ap-core.md`<br>`W03-S5-P13a-hr-timekeeping-employee-portal.md`<br>`W03-S6-P14-fixed-assets.md`<br>`W03-S7-P22b-qa-cross-module.md` | every session printed `RESULT … DONE` or `RESULT … BLOCKED` | step 6 |
| 6 | W03 | 1 | `INTEGRATOR-after-W03.md` | final message `INTEGRATED wave 03` | its NEXT list |
| 7 | W04 | 4 (max 5 at once) | `W04-S1-P10a-sales-ar-crm-core.md`<br>`W04-S2-P11b-requisitions-supplier-portal.md`<br>`W04-S3-P09c-inventory-bom-transformations.md`<br>`W04-S4-P09b-inventory-barcodes-issues.md` | every session printed `RESULT … DONE` or `RESULT … BLOCKED` | step 8 |
| 8 | W04 | 1 | `INTEGRATOR-after-W04.md` | final message `INTEGRATED wave 04` | its NEXT list |
| 9 | W05 | 4 (max 5 at once) | `W05-S1-P16a-treasury-vouchers.md`<br>`W05-S2-P12-pos.md`<br>`W05-S3-P10b-customer-portal.md`<br>`W05-S4-P16b-intercompany-consolidation.md` | every session printed `RESULT … DONE` or `RESULT … BLOCKED` | step 10 |
| 10 | W05 | 1 | `INTEGRATOR-after-W05.md` | final message `INTEGRATED wave 05`; G2 closed | its NEXT list |
| 11 | W06 | 3 (max 5 at once) | `W06-S1-P18-bulk-import-export.md`<br>`W06-S2-P22c-qa-leak-tests.md`<br>`W06-S3-P17a-reporting-semantic-documents.md` | every session printed `RESULT … DONE` or `RESULT … BLOCKED` | step 12 |
| 12 | W06 | 1 | `INTEGRATOR-after-W06.md` | final message `INTEGRATED wave 06` | its NEXT list |
| 13 | W07 | 3 (max 5 at once) | `W07-S1-P19-ai-business-agent.md`<br>`W07-S2-P17b-financial-statements.md`<br>`W07-S3-P17c-exogena.md` | every session printed `RESULT … DONE` or `RESULT … BLOCKED` | step 14 |
| 14 | W07 | 1 | `INTEGRATOR-after-W07.md` | final message `INTEGRATED wave 07` | its NEXT list |
| 15 | W08 | 2 (max 5 at once) | `W08-S1-P21-ai-onboarding-agent.md`<br>`W08-S2-P20-mcp-server.md` | every session printed `RESULT … DONE` or `RESULT … BLOCKED` | step 16 |
| 16 | W08 | 1 | `INTEGRATOR-after-W08.md` | final message `INTEGRATED wave 08`; G3 closed | its NEXT list |
| 17 | W09 | 2 (max 5 at once) | `W09-S1-P23-compliance-evidence.md`<br>`W09-S2-P22d-qa-e2e-a11y-perf.md` | every session printed `RESULT … DONE` or `RESULT … BLOCKED` | step 18 |
| 18 | W09 | 1 | `INTEGRATOR-after-W09.md` | final message `INTEGRATED wave 09`; G4, RC closed | its NEXT list |
| 19 | W10 | 1 (max 5 at once) | `W10-S1-DEV-dev-runtime-local-run.md` | every session printed `RESULT … DONE` or `RESULT … BLOCKED` | step 20 |
| 20 | W10 | 1 | `INTEGRATOR-after-W10.md` | final message `INTEGRATED wave 10` | its NEXT list |

## If something goes wrong

| You see | Do |
|---|---|
| Worker final message `BLOCKED-PRECONDITION` | You pasted too early or the task is already done. Check `docs/run/STATUS.md` on main; paste it when listed. |
| Session expired / closed before `RESULT` | Run the wave's integrator anyway; it lists the task as MISSING. Paste `prompts/RETRY-TASK.md` (it resumes pushed work). |
| Integrator lists `REWORK` | Paste the named `RETRY-TASK.md` in a new session, then the same wave's integrator again (or the next one; both pick it up). |
| `STOP-OPERATOR` (a task failed 3 attempts) | Read the notes in STATUS.md; decide (adjust the environment, accept a reduced scope by editing docs/spec yourself, or stop). |
| `OPERATOR: TASK is BLOCKED` | Remove the named cause (e.g. network level, missing access), then paste its RETRY file. |
| `BLOCKED-AUDIT` | Something pushed to main without the integrator. Paste `prompts/REPAIR-audit-main.md` into a new session, then the same integrator prompt again. |
| `BLOCKED-PUSH` | Re-paste the same integrator prompt in a new session. |
| Usage limit reached | Wait for the reset; start the remaining prompts of the wave later. Nothing is lost: work is on pushed branches. |
