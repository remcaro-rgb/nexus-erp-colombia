# FIX-D-W01-1 progress
attempt: 2

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

Attempt 2 (resumes claude/new-session-l6mzbb):
* Root cause of the red compose job (run 42683848570): the DR drill invariant rls_forced_on_tenant_tables failed for
  kernel_dead_letter, kernel_feature_flag, kernel_idempotency, kernel_outbox, kernel_sequence (no RLS).
* Fix: kernel migration 0002 (requested by CCR P01-5 / P04-4 item 1) enables + forces RLS with the reference policy;
  outbox/dead-letter get an extra `outbox` platform policy used by the relay (tenancy.bind_platform); kernel tests
  read through kernel.testing.tenancy.bound(); new FIX_D_W01_1 RLS tests.
* ci-parity compose-startup now runs dr-drill in its default sequence.
