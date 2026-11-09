# syntax=docker/dockerfile:1
# Nexus ERP frontend image (ADR-013/014). Owner: P03. Static Vite build served by Caddy as uid 10001,
# strict CSP + HSTS + security headers, SPA fallback, no directory listing. Pinned digests (D-028);
# dependencies only from pnpm-lock.yaml (--frozen-lockfile).
# Build context: repository root.   docker build -f deploy/docker/frontend.Dockerfile .
ARG NODE_IMAGE=docker.io/library/node:22.23.4-trixie-slim@sha256:be1e2a3dc2c4fd68cdfadad2a696a280e0d2749098bee509230174d72fb768c6
ARG CADDY_IMAGE=docker.io/library/caddy:2.11.9-alpine@sha256:0bbb055bda6793c999bde78b1b5ce3e89f829fb031af2cf50056ea1246559fee

FROM ${NODE_IMAGE} AS build
ENV PNPM_HOME=/pnpm PATH=/pnpm:$PATH CI=true PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
RUN corepack enable
WORKDIR /src
COPY . .
RUN pnpm install --frozen-lockfile \
 && pnpm --dir frontend run build \
 && test -f frontend/dist/index.html

FROM ${CADDY_IMAGE} AS web
LABEL org.opencontainers.image.source="https://github.com/remcaro-rgb/nexus-erp-colombia"
COPY --chmod=0444 deploy/docker/web.Caddyfile /etc/caddy/Caddyfile
COPY --from=build /src/frontend/dist /srv
ENV XDG_CONFIG_HOME=/tmp/caddy-config XDG_DATA_HOME=/tmp/caddy-data
USER 10001:10001
EXPOSE 8080
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
