# CLOUD-ADDENDUM — executing the 2.2.0-dev specification in Claude Code cloud sessions (BINDING, edition 3.0.0-claude-cloud)

**Precedence.** This addendum replaces only the *execution model* of 2.2.0-dev. Every product rule, feature, test, decision default, UI contract,
dimension rule, security rule and DEV-only restriction in `docs/spec/` and `docs/prompt-pack/` stays exactly as written.

## What is replaced
| 2.2.0-dev (five machines) | 3.0.0-claude-cloud |
|---|---|
| Machines M1–M4 (workers) and M5 (integrator), queues, leases, heartbeats, polling | Ephemeral cloud sessions started by the operator with copy-paste prompts, one task per session, wave by wave (`docs/run/plan-cloud.json`) |
| `erpctl.py`, `COMMANDS.md`, control branch, run ids, `PROTOCOL.md`, M5 adapter | `tools/cloudctl.py` + `docs/run/STATE.json` (written only by the integrator) |
| "M5 accepts / P00 resolves CCRs / gate on M5" | the integrator session of the wave |
| DEV stack on M5 at `localhost` | the DEV task verifies the stack inside a cloud VM as far as the platform allows; the operator runs it on their Mac with `docs/run/LOCAL-RUN.md` |
| Machine column / queues in `docs/spec/plan.json` | void; `docs/spec/plan.json` remains the product task catalogue (ids, owned paths, prompt mapping, gate requirements) |

## Splits (same scope, more sessions)
P09 → P09a/P09b/P09c, P10 → P10a/P10b, P11 → P11a/P11b, P13 → P13a/P13b, P16 → P16a/P16b, P17 → P17a/P17b/P17c. Each part keeps the original prompt
as binding scope and delivers the part named in its `scope`; owned paths are disjoint; every mandatory test and feature belongs to exactly one part
(`cloudctl validate-plan`). The first part of a split ("core") freezes the complete module contract so later parts implement against it.

## Acceptance (never on a worker's word)
1. A worker pushes its branch with `docs/run/reports/<T>.json` (DONE or BLOCKED) as the last commit.
2. The integrator verifies paths, protected files, secrets and report consistency; merges; re-runs `make ci` after each merge and the task's
   mandatory tests itself (`featctl collect` → `docs/run/evidence/W<n>/feature-results.json`); a task is ACCEPTED only if all its tests PASSED there.
3. Gates G0–G4 are closed by the integrator with `featctl gate-check` over every test due by the gate, re-run at the integrated head. Gate-owned
   tests (G0: T-X-QA-07; G2: T-INV-REQ-06, T-PUR-CYCLE-07, T-X-QA-01, T-X-QA-02; G4: T-X-COMPLIANCE-02) are written by the integrator in `tests/gates/`.
4. A failed gate re-opens the owning tasks (attempt + 1). Three failed attempts → FAILED → operator decision.
5. RC = the integrated head after G4, recorded in STATE and pushed as branch `release/rc-<wave>` (the platform's git proxy refuses tags).

## Platform facts this design relies on (see CLOUD-PLATFORM-NOTES.md in the edition for sources and dates)
* Sessions clone the repo fresh, run in isolated Ubuntu 24.04 VMs, can push branches through a GitHub proxy that rejects tag pushes and branch
  deletions but does not restrict branch names; `gh` GraphQL commands are blocked (REST via `gh api` works).
* Docker, docker compose, PostgreSQL 16, Redis 7, Node 22, pnpm, uv and Chrome for Testing are pre-installed. Playwright's own browser CDN is not
  on the default allowlist — use the system Chrome.
* No documented way exists to expose a port of a cloud VM to the operator, so the running app is verified inside the session and delivered for
  local use through `docs/run/LOCAL-RUN.md`.
* Sessions share the operator's plan usage limits; idle VMs pause and can be reclaimed — push work in progress, resume with the RETRY prompt.
