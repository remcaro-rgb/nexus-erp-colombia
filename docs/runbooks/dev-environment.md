# DEV environment runbook

DEV-only, synthetic data, loopback only. Never `docker compose down -v`, never bind 0.0.0.0, never kill a process you did
not start. Secrets live only in `deploy/dev/secrets/` (mode 0600, gitignored); this runbook never prints them.

## Start
```bash
make dev-up          # = scripts/ci/dev-up.sh
make dev-seed        # synthetic demo tenant (seed_demo, P21); prints SKIPPED until that command exists
make dev-health      # JSON report; exit 0 only when every check passes
```
`dev-up` does, in order: Docker preflight (starts `dockerd` itself inside a cloud VM) → port selection
(`ERP_DEV_PORT`, default 8080; else first free of 8081–8090; all busy → exit 3 listing them, `BLOCKED_DEV_RUNTIME`) →
`deploy/dev/runtime.env` (port/origin, no secrets) → `deploy/dev/gen-secrets.sh` → `scripts/ci/build-images.sh`
(only for sources that exist; `NEXUS_BUILD=0` skips, `=1` forces) → infrastructure up and healthy → bucket `erp-dev`
→ `migrate` job as `erp_owner` → application services up and healthy → `/healthz` check.
Environment knobs: `ERP_DEV_PORT`, `ERP_DEV_BIND_HOST` (loopback only: `127.0.0.1` or `::1`), `ERP_IMAGE_TAG` (default
`dev`), `ERP_SECRETS_DIR`, `NEXUS_OBSERVABILITY=1` (Jaeger/Prometheus/Grafana at `/grafana/`), `NEXUS_WAIT_TIMEOUT`.

URLs (port from `deploy/dev/runtime.env`): app `/`, API `/api/`, Keycloak `/auth/` (admin user `nexus-dev-admin`,
password `ERP_KEYCLOAK_ADMIN_PASSWORD` in `dev.env`), Mailpit `/mailpit/` (user `mail`, `ERP_MAILPIT_UI_PASSWORD`),
demo logins: `deploy/dev/secrets/dev-credentials.env`.

## Stop / restart / logs
```bash
make dev-down        # containers + networks removed; volumes (db, keycloak, minio, mailpit) and data kept
scripts/ci/dev-up.sh # restart: same port if still ours, same secrets, data intact
docker compose -p nexus-erp-dev -f deploy/dev/compose.yaml logs -f api
docker compose -p nexus-erp-dev -f deploy/dev/compose.yaml ps
```
`dev-down` refuses `-v`/`--volumes`/`--rmi`. To really start from scratch, remove the named volumes by hand and on
purpose (`docker volume ls --filter label=com.docker.compose.project=nexus-erp-dev`) — never as a routine step.

## Backup / restore (DEV)
```bash
deploy/dev/backup.sh                          # -> ~/.local/share/nexus-erp-dev/backups/erp-<UTC>.dump (0600)
deploy/dev/restore.sh <file>                  # asks you to type "RESTORE erp"
python3 scripts/ci/dr_drill.py                # restore into a fresh isolated container + invariants
```

## Tunnel (reach the loopback-only stack from another machine)
Use SSH port forwarding from your workstation; never publish the port on other interfaces:
```bash
ssh -N -L 8080:127.0.0.1:8080 user@dev-host
```

## Troubleshooting
* `BLOCKED_DEV_RUNTIME: all candidate ports are busy` — free one of 8080–8090 (find the owner with `ss -ltnp`) or set
  `ERP_DEV_PORT` to a free port; the scripts never stop other processes.
* `docker daemon is not reachable` — start Docker Desktop / `sudo dockerd`; in cloud VMs `dev-up` does it.
* Image builds behind an egress proxy: `build-images.sh` passes `HTTPS_PROXY` and the proxy CA (BuildKit secret).
* `api` unhealthy — `docker compose ... logs api`; check `/api/health/live` exists (D-P01, P01).
* Keycloak needs ~60–90 s on first start (healthcheck start period 90 s).
* `make dev-health` lists `not_available_yet` services: their code does not exist yet in this wave (not an error).
