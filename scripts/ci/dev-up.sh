#!/usr/bin/env bash
# make dev-up — start the Nexus ERP DEV stack (D-002, D-P03). Owner: P03.
#  1. docker preflight (starts dockerd itself in a cloud VM if needed)
#  2. port selection: ${ERP_DEV_PORT:-8080}, else first free of 8081..8090 on 127.0.0.1; all busy -> exit 3
#  3. secrets generated into deploy/dev/secrets/ (mode 0600) if missing
#  4. application images built when the sources exist (NEXUS_BUILD=0 to skip, =1 to force)
#  5. infrastructure up + healthy, migrations as erp_owner (explicit job), application up + healthy
# Never binds 0.0.0.0, never kills a process, never removes volumes. Prints URLs and file paths only.
set -euo pipefail
# shellcheck source=scripts/ci/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

WAIT_TIMEOUT="${NEXUS_WAIT_TIMEOUT:-600}"

ensure_docker

# --- 2. port ---------------------------------------------------------------------------------------
owned_ports=""
if [[ -f "$NEXUS_RUNTIME_ENV" ]]; then
  prev_port="$(sed -n 's/^ERP_DEV_PORT=//p' "$NEXUS_RUNTIME_ENV" | tail -1)"
  # the port counts as ours only if our proxy container currently publishes it
  if [[ -n "$prev_port" ]] && docker ps --filter "label=com.docker.compose.project=$NEXUS_PROJECT" \
       --filter "label=com.docker.compose.service=proxy" --format '{{.Ports}}' | grep -q "127.0.0.1:${prev_port}->"; then
    owned_ports="$prev_port"
    export ERP_DEV_PORT="${ERP_DEV_PORT:-$prev_port}"
  fi
fi
set +e
port="$(python3 "$NEXUS_REPO_ROOT/scripts/ci/select_port.py" "$owned_ports")"
rc=$?
set -e
[[ $rc -eq 3 ]] && exit 3
[[ $rc -eq 0 && -n "$port" ]] || die "port selection failed" "$rc"
bind_host="${ERP_DEV_BIND_HOST:-127.0.0.1}"
[[ "$bind_host" == "127.0.0.1" || "$bind_host" == "::1" ]] || die "ERP_DEV_BIND_HOST must be a loopback address (got $bind_host)" 2
umask 077
{
  printf '# written by scripts/ci/dev-up.sh — local runtime selection (no secrets)\n'
  printf 'ERP_DEV_BIND_HOST=%s\n' "$bind_host"
  printf 'ERP_DEV_PORT=%s\n' "$port"
  printf 'ERP_PUBLIC_ORIGIN=http://localhost:%s\n' "$port"
  printf 'ERP_IMAGE_TAG=%s\n' "$ERP_IMAGE_TAG"
  printf 'ERP_SECRETS_DIR=%s\n' "$ERP_SECRETS_DIR"
  printf 'ERP_GIT_SHA=%s\n' "$(git -C "$NEXUS_REPO_ROOT" rev-parse --short=12 HEAD 2>/dev/null || echo unknown)"
} > "$NEXUS_RUNTIME_ENV"
umask 022
unset ERP_DEV_PORT ERP_PUBLIC_ORIGIN
load_runtime_env
log "selected http://localhost:${ERP_DEV_PORT} (bound to ${ERP_DEV_BIND_HOST})"

# --- 3. secrets --------------------------------------------------------------------------------------
"$NEXUS_REPO_ROOT/deploy/dev/gen-secrets.sh" >&2

# --- 4. application images ---------------------------------------------------------------------------
case "${NEXUS_BUILD:-auto}" in
  0) log "NEXUS_BUILD=0: not building application images" ;;
  1) "$NEXUS_REPO_ROOT/scripts/ci/build-images.sh" --force ;;
  *) "$NEXUS_REPO_ROOT/scripts/ci/build-images.sh" ;;
esac

# --- 5. start ----------------------------------------------------------------------------------------
compose_profiles_env
infra=(proxy db valkey keycloak minio mailpit)
[[ ",$COMPOSE_PROFILES," == *",observability,"* ]] && infra+=(otel-collector jaeger prometheus grafana)
log "profiles: ${COMPOSE_PROFILES:-<infrastructure only>}"
log "pulling pinned third-party images (if missing)"
dc pull --quiet --ignore-buildable --policy missing "${infra[@]}" >&2 || dc pull --quiet "${infra[@]}" >&2

log "starting infrastructure: ${infra[*]}"
dc up -d --wait --wait-timeout "$WAIT_TIMEOUT" "${infra[@]}" >&2

# object storage bucket (idempotent; inside the internal network)
dc exec -T minio sh -c 'mc alias set local http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null && mc mb --ignore-existing local/erp-dev >/dev/null && mc version enable local/erp-dev >/dev/null' \
  || log "warning: could not ensure bucket erp-dev"

if [[ ",$COMPOSE_PROFILES," == *",app,"* ]]; then
  log "running migrations (one-shot job, role erp_owner)"
  dc run --rm --no-deps migrate >&2
  mapfile -t apps < <(app_services)
  log "starting application services: ${apps[*]}"
  dc up -d --wait --wait-timeout "$WAIT_TIMEOUT" "${apps[@]}" >&2
else
  log "application images not present: infrastructure + proxy only (api/web appear once their sources exist)"
  [[ ",$COMPOSE_PROFILES," == *",web,"* ]] && dc up -d --wait --wait-timeout "$WAIT_TIMEOUT" web >&2
fi

status="$(http_status "http://127.0.0.1:${ERP_DEV_PORT}/healthz")"
[[ "$status" == "200" ]] || die "proxy health check failed (HTTP $status) on http://127.0.0.1:${ERP_DEV_PORT}/healthz" 4
printf 'DEV stack up: http://localhost:%s  (secrets: %s/dev.env, demo credentials: %s/dev-credentials.env)\n' \
  "$ERP_DEV_PORT" "$ERP_SECRETS_DIR" "$ERP_SECRETS_DIR"
