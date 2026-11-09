#!/usr/bin/env bash
# make dev-health — health of the DEV stack (mirrors deploy/dev/check-health.sh). Owner: P03.
# Prints one JSON object (no secrets). Exit 0 only when every required check passes.
set -euo pipefail
# shellcheck source=scripts/ci/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

ensure_docker
load_runtime_env
[[ -f "$ERP_SECRETS_DIR/dev.env" ]] || die "secrets missing; run make dev-up first" 2
compose_profiles_env
base="http://127.0.0.1:${ERP_DEV_PORT}"

ps_json="$(dc ps --all --format json 2>/dev/null || true)"
NEXUS_APP_SERVICES="$(app_services | paste -sd, -)" NEXUS_PS_JSON="$ps_json" NEXUS_BASE="$base" NEXUS_PROFILES="$COMPOSE_PROFILES" \
NEXUS_SECRETS_DIR="$ERP_SECRETS_DIR" python3 "$NEXUS_REPO_ROOT/scripts/ci/health_report.py"
