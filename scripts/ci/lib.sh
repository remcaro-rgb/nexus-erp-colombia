#!/usr/bin/env bash
# Shared helpers for the Nexus ERP DEV stack scripts (owner: P03).
# DEV-only: binds 127.0.0.1, synthetic data, never prints secrets, never kills foreign processes.
set -euo pipefail

NEXUS_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export NEXUS_REPO_ROOT
NEXUS_COMPOSE_FILE="${NEXUS_COMPOSE_FILE:-$NEXUS_REPO_ROOT/deploy/dev/compose.yaml}"
NEXUS_PROJECT="${NEXUS_PROJECT:-nexus-erp-dev}"
NEXUS_RUNTIME_ENV="${NEXUS_RUNTIME_ENV:-$NEXUS_REPO_ROOT/deploy/dev/runtime.env}"
export ERP_SECRETS_DIR="${ERP_SECRETS_DIR:-$NEXUS_REPO_ROOT/deploy/dev/secrets}"
export ERP_IMAGE_TAG="${ERP_IMAGE_TAG:-dev}"
NEXUS_PORT_RANGE=(8080 8081 8082 8083 8084 8085 8086 8087 8088 8089 8090)

log() { printf '[nexus-dev] %s\n' "$*" >&2; }
die() { local code="${2:-1}"; printf '[nexus-dev] ERROR: %s\n' "$1" >&2; exit "$code"; }

# Docker daemon: use a running one; in a cloud VM where dockerd is installed but not running,
# start it ourselves (a process we own). Never touches other processes.
ensure_docker() {
  command -v docker >/dev/null 2>&1 || die "docker CLI not found" 10
  docker compose version >/dev/null 2>&1 || die "docker compose v2 plugin not found" 10
  if docker info >/dev/null 2>&1; then return 0; fi
  if command -v dockerd >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
    log "docker daemon not running; starting dockerd (log: /tmp/nexus-dockerd.log)"
    sudo -n sh -c 'nohup dockerd > /tmp/nexus-dockerd.log 2>&1 &'
    local i
    for i in $(seq 1 60); do
      if docker info >/dev/null 2>&1; then return 0; fi
      if sudo -n docker info >/dev/null 2>&1; then
        # socket created for root only: let the invoking user reach it
        sudo -n chmod 666 /var/run/docker.sock 2>/dev/null || true
      fi
      sleep 1
    done
  fi
  docker info >/dev/null 2>&1 || die "docker daemon is not reachable (docker info failed)" 10
}

load_runtime_env() {
  if [[ -f "$NEXUS_RUNTIME_ENV" ]]; then
    # shellcheck disable=SC1090
    set -a; . "$NEXUS_RUNTIME_ENV"; set +a
  fi
  export ERP_DEV_BIND_HOST="${ERP_DEV_BIND_HOST:-127.0.0.1}"
  export ERP_DEV_PORT="${ERP_DEV_PORT:-8080}"
  export ERP_PUBLIC_ORIGIN="${ERP_PUBLIC_ORIGIN:-http://localhost:${ERP_DEV_PORT}}"
}

# Run docker compose for the dev project. Secrets are passed as interpolation variables only
# (never echoed); the env files are read by compose itself.
dc() {
  local args=(-p "$NEXUS_PROJECT" -f "$NEXUS_COMPOSE_FILE")
  [[ -f "$NEXUS_RUNTIME_ENV" ]] && args+=(--env-file "$NEXUS_RUNTIME_ENV")
  [[ -f "$ERP_SECRETS_DIR/dev.env" ]] && args+=(--env-file "$ERP_SECRETS_DIR/dev.env")
  docker compose "${args[@]}" "$@"
}

image_present() { docker image inspect "$1" >/dev/null 2>&1; }

# Profiles to enable, derived from which application images exist locally.
active_profiles() {
  local p=()
  image_present "erp-api:${ERP_IMAGE_TAG}" && p+=(app)
  image_present "erp-web:${ERP_IMAGE_TAG}" && p+=(web)
  image_present "erp-mcp:${ERP_IMAGE_TAG}" && p+=(mcp)
  [[ "${NEXUS_OBSERVABILITY:-0}" == "1" ]] && p+=(observability)
  printf '%s\n' "${p[@]:-}"
}

compose_profiles_env() {
  local joined
  joined="$(active_profiles | paste -sd, -)"
  export COMPOSE_PROFILES="$joined"
}

http_status() {
  # prints the HTTP status code (000 on connection failure); never follows redirects off-host
  curl -s -o /dev/null -w '%{http_code}' --max-time "${2:-5}" "$1" || true
}

# Application services that can actually run with the current sources (wave-aware, never faked):
# api always (image present); worker when celery is locked; scheduler/outbox-relay when their management
# commands exist (P07 / P01); mcp when its image exists (P20).
app_services() {
  local s=()
  image_present "erp-api:${ERP_IMAGE_TAG}" || return 0
  s+=(api)
  grep -qiE '^name = "celery"' "$NEXUS_REPO_ROOT/uv.lock" 2>/dev/null && s+=(worker)
  [[ -n "$(find "$NEXUS_REPO_ROOT/backend" -path '*/management/commands/run_scheduler.py' -print -quit 2>/dev/null)" ]] && s+=(scheduler)
  [[ -n "$(find "$NEXUS_REPO_ROOT/backend" -path '*/management/commands/outbox_relay.py' -print -quit 2>/dev/null)" ]] && s+=(outbox-relay)
  image_present "erp-mcp:${ERP_IMAGE_TAG}" && s+=(mcp)
  image_present "erp-web:${ERP_IMAGE_TAG}" && s+=(web)
  printf '%s\n' "${s[@]}"
}
