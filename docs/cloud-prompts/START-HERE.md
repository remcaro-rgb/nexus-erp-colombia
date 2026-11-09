# START HERE — run Nexus ERP Colombia with Claude Code cloud sessions (edition 3.0.0-claude-cloud)

Nothing has been built yet. These files make the build **copy-paste only**: you open sessions and paste prompt files; the sessions do the rest.
Repository: **https://github.com/remcaro-rgb/nexus-erp-colombia** (private).

## One-time setup (about 10 minutes)
1. **Seed the repository** (the main assistant does this for you, or you push `seed.tar.gz` yourself): the first commit on `main` must be the
   seed with the trailer `Nexus-Seed: 3.0.0-claude-cloud`. Check that `main` contains `CLAUDE.md`, `tools/cloudctl.py` and `docs/run/STATE.json`.
2. **Install the Claude GitHub App** on the repository: claude.ai/code → connect GitHub → install the app → "Only select repositories" →
   `remcaro-rgb/nexus-erp-colombia`. (Private repos need the app; alternatively `/web-setup` from a local Claude Code CLI.)
3. **Environment**: at claude.ai/code use the **Default** environment, or create one named `nexus-erp` with network access **Trusted**
   (the default). No secrets and no environment variables are required. Optional (recommended): environment variables
   `BASH_DEFAULT_TIMEOUT_MS=600000` and `BASH_MAX_TIMEOUT_MS=1800000` (longer foreground commands), and this setup script (runs as root, cached):
   `apt-get update && apt-get install -y make && uv python install 3.13` — `make` is not in the documented pre-installed list (sessions install it themselves if missing).
4. **Repository rules on GitHub** (Settings → Rules, optional): you may block force-pushes and branch deletion on `main`. **Do not** require pull
   requests on `main` — the integrator session fast-forwards `main` and would be refused.
5. **Plan usage**: all sessions share your Claude plan limits. Recommended peak: **5 worker sessions at once** (see PARALLELISM.md).

## How to run (every time)
1. Open `PASTE-ORDER.md` (checklist) or `RUNBOOK.md` (table). Start with wave 01.
2. For each file of the wave: claude.ai/code → **new session** → repository `remcaro-rgb/nexus-erp-colombia` → paste the **entire** file → send.
   At most 5 at the same time; when one ends, start the next one of the same wave.
3. A worker is done when its last message starts with `RESULT` (DONE or BLOCKED). You never need to answer questions.
4. When all sessions of the wave have ended: paste `INTEGRATOR-after-WNN.md` into **one** new session. Its final message (`INTEGRATED wave NN`)
   ends with **NEXT**: the exact files to paste next (including any `RETRY-TASK.md`). Follow NEXT; it overrides the happy-path table.
5. Repeat until NEXT says `COMPLETE`. Then follow `docs/run/LOCAL-RUN.md` (in the repo) on your Mac mini to run the app at http://localhost:8080.

## When something goes wrong
* Session expired before `RESULT` → run the integrator anyway; it lists the task as MISSING → paste `RETRY-TASK.md` (resumes pushed work).
* `REWORK` → paste the named `RETRY-TASK.md`, then the integrator again.
* `STOP-OPERATOR` (3 failed attempts) or `OPERATOR: … BLOCKED` → read `docs/run/STATUS.md` on GitHub; fix the named cause (or decide), then paste the RETRY file.
* `BLOCKED-AUDIT` → a commit reached `main` without the integrator; paste `prompts/REPAIR-audit-main.md`, then the integrator again.
* Full table: RUNBOOK.md → "If something goes wrong".

## What is in this package
* `prompts/` — 35 task prompts (`WNN-SK-TASK-name.md`, 34 workers + DEV), 35 `RETRY-TASK.md`, 10 `INTEGRATOR-after-WNN.md`, 1 `REPAIR-audit-main.md`.
* `RUNBOOK.md`, `RUNBOOK.csv`, `PASTE-ORDER.md`, `PARALLELISM.md`, `CLOUD-PLATFORM-NOTES.md`.
* `seed.tar.gz` (+ `.sha256`) — the exact tree of the first commit on `main` (spec, original prompt pack, tools, state, CLAUDE.md; no secrets).

Honest limits: success is not guaranteed; every acceptance is based on tests the integrator re-ran itself; legal externals (DIAN, PILA, rates)
stay labelled synthetic fixtures; DEV-only, never production.
