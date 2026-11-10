# syntax=docker/dockerfile:1
# Nexus ERP backend images (ADR-014, P03 deliverable 1). Owner: P03.
# One build, one target per service: api (gunicorn + uvicorn workers), worker (celery), scheduler (P07 loop),
# outbox-relay, mcp. Multi-stage, slim runtime, non-root uid 10001, base images pinned by digest (D-028),
# dependencies installed only from uv.lock (--frozen: lockfile hashes verified).
# Build context: repository root.   docker build -f deploy/docker/backend.Dockerfile --target api .
ARG PYTHON_IMAGE=docker.io/library/python:3.13.18-slim-trixie@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3 \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY pyproject.toml uv.lock ./
# Optional egress proxy CA (cloud build VMs): BuildKit secret `proxy_ca`, never stored in a layer.
RUN --mount=type=secret,id=proxy_ca,required=false \
    if [ -s /run/secrets/proxy_ca ]; then export SSL_CERT_FILE=/run/secrets/proxy_ca; fi; \
    uv sync --frozen --no-dev --no-install-project --no-editable
COPY backend/ /src/backend/

FROM ${PYTHON_IMAGE} AS runtime
LABEL org.opencontainers.image.source="https://github.com/remcaro-rgb/nexus-erp-colombia" \
      org.opencontainers.image.vendor="Nexus ERP (DEV-only, synthetic data)"
RUN groupadd --system --gid 10001 erp \
 && useradd --system --uid 10001 --gid erp --home-dir /app --shell /usr/sbin/nologin erp \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /opt/venv /opt/venv
COPY --from=build --chown=root:root /src/backend /app
COPY --chmod=0555 deploy/docker/backend-entrypoint.sh /opt/nexus/entrypoint.sh
COPY --chmod=0444 deploy/docker/healthcheck.py /opt/nexus/healthcheck.py
# Contract data read at run time (FIX-UI-LOGIN-W03 UL-1): REPO_DIR = BASE_DIR.parent = / in the image, so the
# permission catalog and the UI module registry live at their repository paths under /. Read-only, root-owned.
COPY --chown=root:root --chmod=0444 contracts/permissions/catalog.yaml /contracts/permissions/catalog.yaml
COPY --chown=root:root --chmod=0444 docs/spec/ui/module-registry.json /docs/spec/ui/module-registry.json
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ERP_ENVIRONMENT=dev
WORKDIR /app
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["/opt/nexus/entrypoint.sh"]

FROM runtime AS api
CMD ["api"]

FROM runtime AS worker
CMD ["worker"]

FROM runtime AS scheduler
CMD ["scheduler"]

FROM runtime AS outbox-relay
CMD ["outbox-relay"]

# MCP server (P20) ships its own package in mcp_server/; build this target only when it exists.
FROM runtime AS mcp
COPY --chown=root:root mcp_server/ /app/mcp_server/
CMD ["mcp"]
