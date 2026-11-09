# FIX-D-W01-1 progress
attempt: 1

Plan per cause:
* D-W01-1 / CI-4: kernel.envconfig = one source of truth (DATABASE_URL, else ERP_DB_* incl. ERP_DB_PASSWORD_FILE);
  ERP_FIELD_ENCRYPTION_KEY[_FILE] read + validated by settings (required in dev profile); compose default
  DJANGO_SETTINGS_MODULE=erp.settings.dev (the empty `erp.settings` package caused "supply the ENGINE value");
  gen-secrets writes deploy/dev/secrets/field-encryption.key (0600, never overwritten).
* CI-1: kernel code fixed (idempotency via ORM, role check SQL in kernel.tenancy, health probe via is_usable,
  linter wording, runtime-built fixtures); one allow-list entry for kernel/io_guard.py (wraps HTTP libs, never calls).
* CI-2: P03 tests job installs `uv sync --frozen` (dev group) and runs pytest from that venv.
* CI-3: devdb bootstraps a safe role over TCP via ERP_DB_ADMIN_URL; workflow sets DATABASE_URL for the service.
* Parity: scripts/ci/ci-parity.sh <policy|p03-tests|make-ci|compose-startup> is the main step of the 4 jobs.
