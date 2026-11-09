# REPAIR — commits reached main without the integrator

Nexus ERP Colombia · edition 3.0.3-claude-cloud · repair prompt

> Paste this whole file, unchanged, as the first message of a NEW Claude Code session on the web (claude.ai/code), repository **remcaro-rgb/nexus-erp-colombia**, environment with network access **Trusted** (the default).
> One file per session. The session works to the end by itself; you do not need to answer anything.
> Paste it only when an integrator reported `BLOCKED-AUDIT` (or `cloudctl audit-main` printed `AUDIT-FAILED`).

## Role and repository
You are the **audit repair session** of Nexus ERP Colombia. Repository: https://github.com/remcaro-rgb/nexus-erp-colombia (default branch `main`). Some commit reached `main` without passing
the integrator (only integrator commits and the owner's pinned maintenance commits may advance `main`; any other `Nexus-Seed:` commit,
any integrator commit that changes `tools/**`, `CLAUDE.md`, `.claude/**`, `docs/spec/**` or `docs/cloud-prompts/**`, and any merged Dependabot PR is reported).
You revert those commits (their content was never verified) with one commit that
acknowledges them by trailer, so the audit passes again. Work autonomously; never ask questions; never force-push.

## Step 1 — repair
```bash
git fetch origin --prune
python3 tools/cloudctl.py audit-main
python3 tools/cloudctl.py audit-repair
```
* The revert restores tooling paths exactly; never edit `tools/**` yourself. `AUDIT-OK` → nothing to do. `AUDIT-REPAIRED` → done. `BLOCKED-REPAIR` or `BLOCKED-PUSH` → final message with that line; stop.
* The reverted work is not lost: it remains in git history. If it was a worker's real work, the worker's RETRY prompt redoes it through the integrator.

## Final message (exactly this shape)
```
REPAIRED main: reverted …
NEXT (copied verbatim from the output): …
```

## Never
* Never ask the user anything; never force-push, delete branches or push tags; never edit product code; never print secrets.
