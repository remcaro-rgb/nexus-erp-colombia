#!/usr/bin/env bash
# CI parity (FIX-D-W01-1). Owner: P03. Single source of truth for the commands of the four GitHub Actions jobs
# in .github/workflows/ci.yml: every step of those jobs is `bash scripts/ci/ci-parity.sh <job> [--step NAME]`,
# so running this script locally runs exactly what CI runs.
#
#   scripts/ci/ci-parity.sh [JOB...] [--step NAME]
#     JOB: policy | p03-tests | make-ci | compose-startup        (default: all four)
#     --step NAME: run only that step of the job (what one workflow step does); default: every step in order
#
# Prints `CI-PARITY <job> exit=N` per job, then `CI-PARITY-OK n/n` or `CI-PARITY-FAILED k/n` (exit 1).
# Variables the workflow already set (DATABASE_URL, ERP_DB_ADMIN_URL, ERP_FIELD_ENCRYPTION_KEY) are honoured;
# missing ones are generated for this run only. Never prints a secret value. Loopback only; never `down -v`.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
cd "$REPO" || exit 2
JOBS_ALL=(policy p03-tests make-ci compose-startup)
PG_IMAGE="docker.io/library/postgres:18.6-alpine@sha256:123b17998ccaf47e53b502e8c6a1e79afd8f579c6f3eec83c563da026f957395"
GITLEAKS_IMAGE="docker.io/zricethezav/gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f"
GRYPE_IMAGE="ghcr.io/anchore/grype:v0.121.2@sha256:6a36234da13f9faa327306ac0b3dab3405cfa2b4eb20c8f904125a8707863913"

log() { printf '[ci-parity] %s\n' "$*" >&2; }
gen_key() { python3 -c 'import base64,secrets;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())'; }
gen_pw() { python3 -c 'import secrets;print(secrets.token_hex(24))'; }
mask() { [[ -n "${GITHUB_ACTIONS:-}" ]] && printf '::add-mask::%s\n' "$1"; return 0; }

STEP=""
JOBS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --step) STEP="${2:?--step needs a name}"; shift 2 ;;
    policy|p03-tests|make-ci|compose-startup) JOBS+=("$1"); shift ;;
    *) printf 'usage: %s [policy|p03-tests|make-ci|compose-startup]... [--step NAME]\n' "$0" >&2; exit 2 ;;
  esac
done
[[ ${#JOBS[@]} -eq 0 ]] && JOBS=("${JOBS_ALL[@]}")
[[ -n "$STEP" && ${#JOBS[@]} -ne 1 ]] && { echo "--step needs exactly one job" >&2; exit 2; }

# step NAME CMD... : runs CMD (in this shell, errexit) when no --step filter is set or it names this step.
step() {
  local name="$1"; shift
  [[ -n "$STEP" && "$STEP" != "$name" ]] && return 0
  log "step $name"
  "$@"
}

field_key_env() {
  if [[ -z "${ERP_FIELD_ENCRYPTION_KEY:-}" && -z "${ERP_FIELD_ENCRYPTION_KEY_FILE:-}" ]]; then
    ERP_FIELD_ENCRYPTION_KEY="$(gen_key)"; mask "$ERP_FIELD_ENCRYPTION_KEY"; export ERP_FIELD_ENCRYPTION_KEY
    log "ERP_FIELD_ENCRYPTION_KEY generated for this run (not printed)"
  fi
}

# ---------------------------------------------------------------------------------------------- policy
policy_shellcheck() {
  uvx --from shellcheck-py==0.11.0.1 shellcheck -x -S warning scripts/ci/*.sh deploy/dev/*.sh deploy/dev/db/init/*.sh deploy/docker/*.sh
}
policy_compose() {
  docker compose -f deploy/dev/compose.yaml --env-file deploy/dev/dev.env.example --profile '*' config -q
  python3 scripts/ci/lint_compose.py
}
policy_gitleaks() {
  docker run --rm -v "$PWD:/repo" -w /repo "$GITLEAKS_IMAGE" git --redact --no-banner /repo
}
policy_semgrep() {
  uvx --from semgrep==1.142.0 semgrep scan --error --metrics=off --config p/django --config p/python --config p/secrets backend
}
policy_audit() {
  local req; req="$(mktemp)"
  uv export --frozen --no-hashes --format requirements-txt > "$req"
  uvx --from pip-audit==2.9.0 pip-audit --strict -r "$req"
  rm -f "$req"
  if [[ -f pnpm-lock.yaml ]]; then corepack enable && pnpm audit --audit-level critical; fi
}
policy_licenses() {
  local venv; venv="$(mktemp -d)/venv"
  UV_PROJECT_ENVIRONMENT="$venv" uv sync --frozen --no-dev --quiet
  uvx --from pip-licenses==5.0.0 pip-licenses --python "$venv/bin/python" \
    --fail-on 'GNU Affero General Public License v3 (AGPLv3);GNU General Public License v3 (GPLv3)'
}
job_policy() {
  set -e
  # forbidden-pattern scanners, each over its whole default scope (backend/ + mcp_server/; repo files for secrets)
  step forbidden-patterns python3 scripts/ci/forbidden_patterns.py
  step static-checks python3 scripts/ci/static_checks.py
  step compose-lint policy_compose
  step shellcheck policy_shellcheck
  step otel-scrub python3 scripts/ci/render_otel_scrub.py --check
  step secret-scan python3 scripts/ci/secret_scan.py
  step gitleaks policy_gitleaks
  step semgrep policy_semgrep
  step dependency-audit policy_audit
  step licenses policy_licenses
}

# ------------------------------------------------------------------------------------------- p03-tests
job_p03_tests() {
  set -e
  # fresh environment installed exactly as CI does (uv sync --frozen, dev group included) inside run-tests.sh
  local venv="${NEXUS_PARITY_VENV:-}"
  if [[ -z "$venv" ]]; then
    if [[ -n "${GITHUB_ACTIONS:-}" ]]; then venv="$REPO/.venv"; else venv="$(mktemp -d)/venv"; fi
  fi
  step pytest env UV_PROJECT_ENVIRONMENT="$venv" bash scripts/ci/run-tests.sh
}

# --------------------------------------------------------------------------------------------- make-ci
PG_CONTAINER=""
cleanup_pg() { [[ -n "$PG_CONTAINER" ]] && docker rm -f "$PG_CONTAINER" >/dev/null 2>&1; PG_CONTAINER=""; }
local_tcp_postgres() {
  # Actions-like: a PostgreSQL container on 127.0.0.1 (TCP, no unix socket), credentials generated for the run.
  local admin_pw app_pw port
  admin_pw="$(gen_pw)"; app_pw="$(gen_pw)"
  port="$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')"
  PG_CONTAINER="nexus-ci-parity-pg-$$"
  docker run -d --rm --name "$PG_CONTAINER" -p "127.0.0.1:${port}:5432" -e POSTGRES_PASSWORD="$admin_pw" \
    "$PG_IMAGE" >/dev/null
  local _
  for _ in $(seq 1 60); do
    docker exec "$PG_CONTAINER" pg_isready -U postgres -h 127.0.0.1 >/dev/null 2>&1 && break
    sleep 1
  done
  docker exec "$PG_CONTAINER" pg_isready -U postgres -h 127.0.0.1 >/dev/null
  export ERP_DB_ADMIN_URL="postgres://postgres:${admin_pw}@127.0.0.1:${port}/postgres"
  export DATABASE_URL="postgres://nexus_ci:${app_pw}@127.0.0.1:${port}/nexus_erp"
  log "PostgreSQL for this run on 127.0.0.1:${port} (credentials generated, not printed)"
}
mc_make_ci() { make ci; }
mc_migrations() { uv run --frozen python backend/manage.py makemigrations --check --dry-run; }
mc_typecheck() {
  if grep -q '^name = "mypy"' uv.lock; then uv run --frozen mypy backend; else echo "mypy not locked yet"; fi
  if [[ -f frontend/tsconfig.json ]]; then pnpm --dir frontend exec tsc --noEmit; else echo "frontend not present yet"; fi
}
mc_frontend() {
  if [[ -f frontend/package.json ]]; then pnpm --dir frontend run build && pnpm --dir frontend exec vitest run
  else echo "frontend not present yet"; fi
}
job_make_ci() {
  set -e
  if [[ -z "${DATABASE_URL:-}" ]]; then
    trap cleanup_pg EXIT
    local_tcp_postgres
  fi
  case "${DATABASE_URL}" in *"host=/"*|postgres:///*) echo "make-ci parity needs a TCP DATABASE_URL" >&2; return 2 ;; esac
  field_key_env
  if [[ -z "${GITHUB_ACTIONS:-}" ]]; then uv sync --frozen --quiet; fi
  step make-ci mc_make_ci
  step makemigrations mc_migrations
  step typecheck mc_typecheck
  step frontend mc_frontend
}

# ------------------------------------------------------------------------------------- compose-startup
cs_health() { bash scripts/ci/dev-health.sh | tee reports-health.json; }
cs_sbom() {
  bash scripts/ci/build-images.sh --sbom
  local img
  for img in $(docker image ls --format '{{.Repository}}:{{.Tag}}' | grep '^erp-'); do
    docker run --rm -v /var/run/docker.sock:/var/run/docker.sock "$GRYPE_IMAGE" "docker:$img" --fail-on critical --only-fixed
  done
}
cs_logs() { docker compose -p nexus-erp-dev -f deploy/dev/compose.yaml ps -a; }
job_compose_startup() {
  set -e
  # T-P03-01: compose up (images, migrate) + seed + health, then the DR drill and the image scan of the same job.
  step dev-up bash scripts/ci/dev-up.sh
  step dev-seed bash scripts/ci/dev-seed.sh
  step dev-health cs_health
  step dr-drill python3 scripts/ci/dr_drill.py
  step image-scan cs_sbom
  # the next two only when asked by name (workflow: on failure / always); never `down -v`
  if [[ "$STEP" == "logs" ]]; then cs_logs; fi
  if [[ "$STEP" == "dev-down" ]]; then bash scripts/ci/dev-down.sh; fi
}

# ------------------------------------------------------------------------------------------------ main
ok=0; n=0; failed=()
for job in "${JOBS[@]}"; do
  n=$((n + 1))
  fn="job_${job//-/_}"
  ( "$fn" )
  rc=$?
  printf 'CI-PARITY %s exit=%s\n' "$job" "$rc"
  if [[ $rc -eq 0 ]]; then ok=$((ok + 1)); else failed+=("$job"); fi
done
if [[ $ok -eq $n ]]; then
  printf 'CI-PARITY-OK %s/%s\n' "$ok" "$n"
  exit 0
fi
printf 'CI-PARITY-FAILED %s/%s (%s)\n' "$((n - ok))" "$n" "${failed[*]}"
exit 1
