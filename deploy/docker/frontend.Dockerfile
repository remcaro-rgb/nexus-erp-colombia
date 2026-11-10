# syntax=docker/dockerfile:1
# Nexus ERP frontend image (ADR-013/014). Owner: P03. Static Vite build served by Caddy as uid 10001,
# strict CSP + HSTS + security headers, SPA fallback, no directory listing. Pinned digests (D-028);
# dependencies only from pnpm-lock.yaml (--frozen-lockfile).
# Build context: repository root.   docker build -f deploy/docker/frontend.Dockerfile .
ARG NODE_IMAGE=docker.io/library/node:22.23.4-trixie-slim@sha256:be1e2a3dc2c4fd68cdfadad2a696a280e0d2749098bee509230174d72fb768c6
ARG CADDY_IMAGE=docker.io/library/caddy:2.11.9-alpine@sha256:0bbb055bda6793c999bde78b1b5ce3e89f829fb031af2cf50056ea1246559fee

FROM ${NODE_IMAGE} AS build
ENV PNPM_HOME=/pnpm PATH=/pnpm:$PATH CI=true PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
WORKDIR /src
# 1. Package manifests first (CCR P08-4): corepack reads `packageManager` from package.json, and the
#    dependency layer is cached until the lockfile changes. pnpm-workspace.yaml is optional (glob).
COPY package.json pnpm-lock.yaml pnpm-workspace.yam[l] ./
COPY frontend/package.json frontend/package.json
# Optional egress proxy CA (cloud build VMs): BuildKit secret `proxy_ca`, never stored in a layer.
# 2. corepack (the pinned pnpm), 3. dependencies from the frozen lockfile into the store (`pnpm fetch`
#    needs only the lockfile; the root postinstall needs the sources, so it runs in step 5).
RUN --mount=type=secret,id=proxy_ca,required=false \
    if [ -s /run/secrets/proxy_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/proxy_ca; fi; \
    corepack enable && corepack prepare --activate \
 && pnpm fetch --frozen-lockfile
# 4. The rest of the sources, 5. offline install from the fetched store (frozen lockfile) + build.
COPY . .
RUN pnpm install --frozen-lockfile --offline \
 && pnpm --dir frontend run build \
 && test -f frontend/dist/index.html

FROM ${CADDY_IMAGE} AS web
LABEL org.opencontainers.image.source="https://github.com/remcaro-rgb/nexus-erp-colombia"
# FIX-IMG-W02 (CCR P08-7): the pinned caddy base (Alpine 3.24.4) ships zlib 1.3.2-r1 = CVE-2026-4738 (critical, fixed
# in 1.4-r0), which fails the CI image scan (grype --fail-on critical --only-fixed). Upgrade only zlib, with a minimum
# version so the build fails instead of silently shipping the vulnerable one. Proxy CA as in backend.Dockerfile.
RUN --mount=type=secret,id=proxy_ca,required=false \
    if [ -s /run/secrets/proxy_ca ]; then export SSL_CERT_FILE=/run/secrets/proxy_ca; fi; \
    apk add --no-cache --upgrade 'zlib>=1.4-r0'
COPY --chmod=0444 deploy/docker/web.Caddyfile /etc/caddy/Caddyfile
COPY --from=build /src/frontend/dist /srv
ENV XDG_CONFIG_HOME=/tmp/caddy-config XDG_DATA_HOME=/tmp/caddy-data
USER 10001:10001
EXPOSE 8080
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
