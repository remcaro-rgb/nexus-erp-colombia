# tools (Python 3.11+, standard library only; integrator/seed only — workers never edit tools/)

* `cloudctl.py` — plan validation, worker start/check/report, integrator integrate/close-gates/publish, next/status, audit-main/audit-repair. `python3 tools/cloudctl.py -h`
  audit-main accepts on `main` only integrator commits that leave tooling untouched, audit-repair reverts, and the owner's maintenance commits pinned
  in `cloudctl.py` (seed 19a77d08, 3.0.1 da85d6c9 by SHA; the single 3.0.2 commit and, after it, the single 3.0.3 commit by trailer + exact file
  hashes). Detection, not authentication. Worker branches are `claude/*` only; `dependabot/*` and other branches are ignored.
  Since 3.0.3 `integrate` runs `python3 tools/parity_gate.py` after each merge (default `--test-cmd`).
* `parity_gate.py` (3.0.3) — the integrator's CI-parity gate: installs the locked deps and runs `scripts/ci/ci-parity.sh` (the four GitHub
  Actions jobs: policy, p03-tests, make-ci incl. mypy over backend/ and tsc, compose-startup). Last line `PARITY-GATE OK …` /
  `PARITY-GATE FAILED (merge of PXX) …` naming the failing jobs, directories and owner tasks (the REWORK reason). Failing-job excerpts
  (scrubbed) go to `docs/run/evidence/WNN/parity-gate/`. `--final` before publish (skips when only docs/ changed since a passing run).
  Workers may RUN it as a self-check (Step 5b of their prompts); nobody but the owner edits it.
* `dev_login.py` (3.0.3) — `python3 tools/dev_login.py ensure`: throw-away DEV login `ui-preview` for the UI preview until P21's demo tenant
  exists (Keycloak user in realm erp-dev + `manage.py dev_ui_principal`); fresh random password only in the gitignored
  `deploy/dev/secrets/ui-preview.env` (0600); loopback stack only; never prints the password.
* `featctl.py` — feature registry: `validate --root docs/spec`, `required`, `gate-check`, `collect` (JUnit XML / Playwright JSON → feature results)
* `uictl.py` — UI overlay validation: `validate --root docs/spec`
* `ui_snapshot.py` + `ui_snapshot.cjs` — integrator-only UI progress screenshots after each wave (system Chrome via a pinned playwright-core,
  no browser download) into `docs/ui-progress/WNN/` + `INDEX.md`; never blocks integration. `python3 tools/ui_snapshot.py --wave N --commit`
  Base URL = `ERP_PUBLIC_ORIGIN` (the OIDC origin); without a demo login it bootstraps the `dev_login.py` user.
* `test_cloudctl.py` — unit tests: `python3 -m unittest discover -s tools -p 'test_*.py'`
