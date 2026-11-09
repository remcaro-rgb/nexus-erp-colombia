#!/usr/bin/env bash
# Build the application images erp-<service>:${ERP_IMAGE_TAG} from the repository sources. Owner: P03.
# Builds only what has sources (wave-aware): backend targets when pyproject.toml + uv.lock + backend/manage.py
# exist; mcp when mcp_server/ exists; web when package.json + pnpm-lock.yaml + frontend/package.json exist.
# Missing sources are reported, not faked. Build failures fail the script.
#   --force   rebuild without cache      --sbom   also write CycloneDX SBOMs to reports/sbom/ (syft image)
set -euo pipefail
# shellcheck source=scripts/ci/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

force=0; sbom=0
for a in "$@"; do
  case "$a" in --force) force=1 ;; --sbom) sbom=1 ;; *) die "unknown option $a" 2 ;; esac
done
ensure_docker
cd "$NEXUS_REPO_ROOT"
sha="$(git rev-parse --short=12 HEAD 2>/dev/null || echo unknown)"
common=(--label "org.opencontainers.image.revision=$sha" --label "nexus.environment=dev")
[[ $force -eq 1 ]] && common+=(--no-cache)
# Sessions behind an egress proxy on the host loopback (cloud VMs): build on the host network with the proxy
# variables (Docker predefined build args, not persisted) and the proxy CA as a BuildKit secret.
if [[ -n "${HTTPS_PROXY:-${https_proxy:-}}" ]]; then
  proxy="${HTTPS_PROXY:-${https_proxy:-}}"
  common+=(--network host --build-arg "HTTPS_PROXY=$proxy" --build-arg "https_proxy=$proxy"
           --build-arg "NO_PROXY=${NO_PROXY:-localhost,127.0.0.1}")
  ca="${NEXUS_BUILD_CA_FILE:-${SSL_CERT_FILE:-}}"
  [[ -z "$ca" && -f /root/.ccr/ca-bundle.crt ]] && ca=/root/.ccr/ca-bundle.crt
  [[ -n "$ca" && -f "$ca" ]] && common+=(--secret "id=proxy_ca,src=$ca")
  log "building through the configured HTTPS proxy"
fi
built=()

build() { # target image dockerfile
  log "building $2 (target $1)"
  DOCKER_BUILDKIT=1 docker build -q "${common[@]}" -f "$3" --target "$1" -t "$2" . >/dev/null
  built+=("$2")
}

if [[ -f pyproject.toml && -f uv.lock && -f backend/manage.py ]]; then
  for t in api worker scheduler outbox-relay; do
    build "$t" "erp-$t:${ERP_IMAGE_TAG}" deploy/docker/backend.Dockerfile
  done
  if [[ -d mcp_server ]]; then
    build mcp "erp-mcp:${ERP_IMAGE_TAG}" deploy/docker/backend.Dockerfile
  else
    log "mcp_server/ not present yet (P20): MCP is served by the api at /mcp/"
  fi
else
  log "backend sources not present yet (pyproject.toml, uv.lock, backend/manage.py): backend images not built"
fi

if [[ -f package.json && -f pnpm-lock.yaml && -f frontend/package.json ]]; then
  build web "erp-web:${ERP_IMAGE_TAG}" deploy/docker/frontend.Dockerfile
else
  log "frontend sources not present yet (package.json, pnpm-lock.yaml, frontend/package.json): web image not built"
fi

if [[ $sbom -eq 1 && ${#built[@]} -gt 0 ]]; then
  mkdir -p reports/sbom
  syft_image="docker.io/anchore/syft:v1.57.0@sha256:3c70e6d5979364cefdc22b51617a78fd264574d3bd6b4f84a351204147025e2a"
  for img in "${built[@]}"; do
    out="reports/sbom/${img%%:*}.cdx.json"
    docker run --rm -v /var/run/docker.sock:/var/run/docker.sock "$syft_image" "docker:$img" -o cyclonedx-json > "$out"
    log "SBOM $out"
  done
fi

for img in "${built[@]:-}"; do
  [[ -z "$img" ]] && continue
  size=$(docker image inspect "$img" --format '{{.Size}}')
  user=$(docker image inspect "$img" --format '{{.Config.User}}')
  printf '%s size=%sMB user=%s\n' "$img" "$((size / 1024 / 1024))" "${user:-root}"
done
