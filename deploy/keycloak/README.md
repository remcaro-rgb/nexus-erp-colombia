# deploy/keycloak — realm import directory

Mounted read-only into Keycloak at `/opt/keycloak/data/import` (`start-dev --import-realm`).
`realm-template.json` is **owned by P04** (realm `erp-dev`, no passwords, clients `erp-web`, `erp-mcp`, `erp-smoke`,
`erp-portal`). It may use `${ERP_PUBLIC_ORIGIN}`, `${ERP_SMOKE_CLIENT_SECRET}`, `${ERP_MCP_CLIENT_SECRET}` and
`${ERP_API_OIDC_CLIENT_SECRET}` placeholders: compose passes those variables to Keycloak (deploy/dev/compose.yaml).
Import happens when the realm does not exist yet; to re-import after a template change, delete the realm in the admin
console (dev only) or recreate the `keycloak-data` volume deliberately — `make dev-down` never removes it.
