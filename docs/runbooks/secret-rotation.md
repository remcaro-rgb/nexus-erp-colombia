# Secret rotation

DEV: secrets are generated once into `deploy/dev/secrets/dev.env` (0600). To rotate one value in DEV, delete its line and
run `deploy/dev/gen-secrets.sh` (it appends only missing keys), then:
* DB role passwords: `ALTER ROLE <role> PASSWORD '<new>'` as `postgres` inside the db container, then `make dev-up`
  (containers pick up the new env).
* `ERP_SECRET_KEY`: restart api/worker; sessions are invalidated.
* OIDC client secrets / Keycloak admin: change in Keycloak (admin console) to the new value, restart api.
* Valkey / MinIO: restart those services with the new value (`make dev-up`).
Never paste values in chat, tickets or logs. Production reference: secrets in Vault/OpenBao or cloud KMS, dual-secret
rotation windows, audit of every read (ADR-012).
