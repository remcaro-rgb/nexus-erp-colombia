# CLOUD-PLATFORM-NOTES — what this edition relies on (read 2026-11-09)

Edition 3.0.0-claude-cloud. Every fact below was read on **2026-11-09** in the official Claude Code documentation. Where the documentation is
silent, the design is defensive and says so. Re-check these pages if the product changes; nothing here was tested in a real cloud session.

| # | Source (read 2026-11-09) |
|---|---|
| S1 | https://code.claude.com/docs/en/claude-code-on-the-web |
| S2 | https://code.claude.com/docs/en/cloud-environments |
| S3 | https://code.claude.com/docs/en/web-quickstart |
| S4 | https://code.claude.com/docs/en/claude-projects |
| S5 | https://code.claude.com/docs/en/hooks (SessionStart) · https://code.claude.com/docs/en/settings · https://code.claude.com/docs/en/memory (CLAUDE.md) |

## Findings and how the design uses them

| Topic | Documented (source) | Design consequence |
|---|---|---|
| Repo access | GitHub access through the **Claude GitHub App** installed on the repository, or `/web-setup` (sends your local `gh` token). Credentials stay outside the VM; a GitHub proxy injects them (S1, S2). | START-HERE step 1: install the Claude GitHub App on `remcaro-rgb/nexus-erp-colombia` (private repo). |
| Branch pushes | The proxy **rejects branch deletions and pushes of anything other than a branch (e.g. tags)**. It **does not limit which branches a push can update**; use GitHub branch protection/rulesets for that (S2). | No tags: the RC is branch `release/rc-09`. Workers *can* technically push `main` (same account) → forbidden by prompt + CLAUDE.md + `.claude/settings.json` deny rules, and **detected** by `cloudctl audit-main` (every first-parent commit after the seed must carry `Nexus-Integrator:`). |
| PRs / GitHub API | GraphQL is blocked (403); `gh pr` / `gh issue` fail; REST through `gh api repos/OWNER/REPO/...` works (S2). PRs can be created from the web UI (S1). | Acceptance never depends on PRs; the integrator merges locally and fast-forwards `main`. |
| Branch names | Sessions work on a `claude/...` branch (S1). **Silent:** whether names are forced/stable. | `worker-start` uses the session's `claude/...` branch or creates `claude/TASK-aN-xxxx`; the integrator finds reports on **any** branch. |
| Session duration / expiry | Idle VMs pause after a few minutes and can be **reclaimed** ("Environment expired"); reopening restores the conversation, not running processes (S1, S2). **Silent:** a hard maximum session length. | Workers push WIP + `docs/run/progress/TASK.md` (`attempt: N`) every ≤45 min; `RETRY-TASK.md` resumes from it. STATE lives in the repo. |
| Concurrency | Each cloud task runs in its own session; parallel sessions share your plan's rate limits and use them faster (S1, S4). **No fixed concurrency number is documented.** | Recommended peak = 5 workers (PARALLELISM.md, planning estimate). Lower it if limits bite; the runbook still works. |
| Command time limits | Bash foreground default 2 min, up to 10 min; `BASH_DEFAULT_TIMEOUT_MS` / `BASH_MAX_TIMEOUT_MS` environment variables can raise them (S2). SessionStart `command` hooks cancelled after 600 s unless `timeout` set (S2, S5). | Long tests/integration run with `nohup … &` and polling. Hook is fast (backgrounds installs, timeout 60). |
| Network modes | None / **Trusted (default)** / Full / Custom. Trusted = allowlisted package registries, GitHub, cloud SDKs, Docker Hub, ghcr.io, mcr.microsoft.com, Ubuntu apt, … (S2). | Use **Trusted**. Playwright's browser CDN is not on the list → system Chrome (`$CHROME_PATH`) and `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`. Keycloak image from Docker Hub (`docker.io/keycloak/keycloak`), not quay.io. |
| Setup scripts | Bash, run as root before Claude Code launches; cached as a filesystem snapshot if they finish in ~5 min; cache rebuilt on change or after ~7 days (S2). | Optional (START-HERE): `uv python install 3.13`. Not required. |
| SessionStart hooks | From the repo's `.claude/settings.json`; run on every start/resume; `CLAUDE_CODE_REMOTE=true` in cloud sessions (S2, S5). | `scripts/cloud/session-start.sh` (cloud-only, never fails, background `uv sync`/`pnpm install`). |
| Env vars / secrets | Environment variables and setup script are readable by anyone using the environment — not for secrets; network secrets are injected by the proxy outside the VM (S2). | No secrets needed or stored. Dev secrets are generated at run time into gitignored files. |
| Installed tools | Ubuntu 24.04; Python 3.x + uv/pip/poetry; Node 20/21/22 + npm/yarn/pnpm; **Docker, dockerd, docker compose**; **PostgreSQL 16, Redis 7** (not running; `service postgresql start`); Chrome for Testing at `/usr/bin/google-chrome` with `CHROME_PATH`; gh (S2). | Python 3.13 installed by uv; Postgres/Redis started by sessions; Docker used for the dev stack. |
| Resources | ~4 vCPU, 16 GB RAM, 30 GB disk (S2). | `make ci` must stay < ~15 min; DEV verification may be partial and is reported honestly. |
| Session identity | `CLAUDE_CODE_REMOTE_SESSION_ID`; transcript URL `https://claude.ai/code/session_…` (S2). | Recorded in each report (`session_url`). |
| CLAUDE.md / rules | The repo's `CLAUDE.md`, `.claude/settings.json`, `.claude/rules` are loaded in cloud sessions (S1, S5). | Seed ships concise binding `CLAUDE.md` + deny rules. |
| Permission modes | Cloud sessions offer Auto, Accept edits, Plan (S1). | Use the default/Auto; prompts never require approvals. |
| Prefilled links | `https://claude.ai/code?prompt=…&repositories=owner/repo` (plus `prompt_url` needing CORS, `environment`) (S3). | Not used for the prompts (too long for a URL); START-HERE gives the plain repository link. |

## Silent / uncertain (designed defensively)
* **`make`**: the installed-tools table lists GCC, Clang, cmake, ninja — not GNU make. Sessions run `command -v make || sudo apt-get install -y make` (hook, prompts, optional setup script). Whether `sudo` is available to the session user is not documented; setup scripts do run as root.
* **Exposing a VM port to you**: not documented → the app is verified inside the session (curl + Playwright on 127.0.0.1) and you run it on your Mac with `docs/run/LOCAL-RUN.md`.
* **Docker-in-VM limits** (nested virtualization, image size, build time): only "Docker is available" is documented → DEV records exactly what ran; partial verification is reported, not hidden.
* **Hard session length / max concurrent sessions**: not documented as numbers → WIP pushes, RETRY prompts, k=5 is an estimate.
* **Pushing to `main` from a session**: the proxy does not restrict branches, so the integrator's fast-forward push should work; if you add a GitHub rule that requires pull requests on `main`, the integrator's `publish` will be refused (`BLOCKED-PUSH`). Keep `main` pushable (no "require PR"); optionally block force-pushes and deletions.
