#!/usr/bin/env bash
# make dev-seed — load the synthetic demo tenant (D-P21) into the running DEV stack. Owner: P03 (wiring);
# the seed itself is `manage.py seed_demo --synthetic --idempotent` owned by P21. Safe to re-run.
# Demo passwords are read by the seed from the mounted dev-credentials.env; nothing is printed.
# Exit: 0 seeded (or nothing to seed yet, stated clearly) · 1 seed failed · 2 stack not running.
set -euo pipefail
# shellcheck source=scripts/ci/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

ensure_docker
load_runtime_env
[[ -f "$ERP_SECRETS_DIR/dev.env" ]] || die "secrets missing; run make dev-up first" 2
compose_profiles_env

if ! dc ps --status running --services 2>/dev/null | grep -qx db; then
  die "the DEV stack is not running; run make dev-up first" 2
fi

seed_cmd="$NEXUS_REPO_ROOT/backend/onboarding/management/commands/seed_demo.py"
if [[ ",$COMPOSE_PROFILES," != *",app,"* ]]; then
  printf 'dev-seed: SKIPPED — application image erp-api:%s not present (backend not built yet); nothing to seed.\n' "$ERP_IMAGE_TAG"
  exit 0
fi
if [[ ! -f "$seed_cmd" ]]; then
  printf 'dev-seed: SKIPPED — seed_demo command not present yet (owned by P21, %s); nothing to seed.\n' "${seed_cmd#"$NEXUS_REPO_ROOT"/}"
  exit 0
fi

log "running seed_demo --synthetic --idempotent"
dc run --rm --no-deps seed >&2
printf 'dev-seed: OK (synthetic demo tenant; credentials file: %s/dev-credentials.env)\n' "$ERP_SECRETS_DIR"
