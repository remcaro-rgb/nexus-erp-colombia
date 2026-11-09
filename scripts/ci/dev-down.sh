#!/usr/bin/env bash
# make dev-down — stop the DEV stack. Owner: P03.
# Removes the project's containers and networks only. NEVER passes -v: named volumes (database, Keycloak,
# MinIO, Mailpit) and therefore all seeded data are kept. Secrets files are kept.
set -euo pipefail
# shellcheck source=scripts/ci/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

for arg in "$@"; do
  case "$arg" in
    -v|--volumes|--rmi) die "refusing '$arg': dev-down never deletes volumes or images" 2 ;;
  esac
done

if ! docker info >/dev/null 2>&1; then
  log "docker daemon not reachable: nothing to stop"
  exit 0
fi
load_runtime_env
# all profiles, so every service of the project is stopped
export COMPOSE_PROFILES="app,web,mcp,observability,tools"
if [[ ! -f "$ERP_SECRETS_DIR/dev.env" ]]; then
  log "no secrets file: stack was never started"
  exit 0
fi
dc down --remove-orphans >&2
log "stopped; volumes kept: $(docker volume ls -q --filter "label=com.docker.compose.project=$NEXUS_PROJECT" | paste -sd' ' -)"
