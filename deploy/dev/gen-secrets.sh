#!/usr/bin/env bash
# Generate the DEV secrets files (D-P03, ADR-012). Owner: P03.
#   $ERP_SECRETS_DIR/dev.env              service secrets (DB roles, Valkey, Django, Keycloak admin, MinIO, OIDC clients)
#   $ERP_SECRETS_DIR/dev-credentials.env  one random password per synthetic demo user (read by seed_demo, P21)
#   $ERP_SECRETS_DIR/field-encryption.key ERP_FIELD_ENCRYPTION_KEY (url-safe base64 of 32 bytes), read by the
#                                         settings via ERP_FIELD_ENCRYPTION_KEY_FILE / by the compose wrapper
#                                         (FIX-D-W01-1). Created once; NEVER overwritten (encrypted dev data must
#                                         stay readable). A key already present in dev.env (older runs) is reused.
# Rules: umask 077, files mode 0600, existing values are NEVER overwritten (missing keys are appended),
# random values come from the OS CSPRNG, and only file paths are printed — never values.
set -euo pipefail
umask 077

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ERP_SECRETS_DIR="${ERP_SECRETS_DIR:-$HERE/secrets}"
mkdir -p "$ERP_SECRETS_DIR"
chmod 700 "$ERP_SECRETS_DIR"

rand_hex() { # $1 = bytes
  if command -v openssl >/dev/null 2>&1; then openssl rand -hex "$1"
  else python3 -c "import secrets,sys;print(secrets.token_hex(int(sys.argv[1])))" "$1"; fi
}
rand_b64key() { python3 -c "import base64,secrets;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"; }

# ensure_key FILE KEY GENERATOR...  — appends KEY=<generated> only when KEY is absent.
ensure_key() {
  local file="$1" key="$2"; shift 2
  if [[ -f "$file" ]] && grep -q "^${key}=" "$file"; then return 0; fi
  local value; value="$("$@")"
  printf '%s=%s\n' "$key" "$value" >> "$file"
}

DEV_ENV="$ERP_SECRETS_DIR/dev.env"
CRED_ENV="$ERP_SECRETS_DIR/dev-credentials.env"
created=()
for f in "$DEV_ENV" "$CRED_ENV"; do
  if [[ ! -f "$f" ]]; then
    printf '# Nexus ERP DEV secrets — generated %s — never commit, never paste.\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$f"
    created+=("$f")
  fi
  chmod 600 "$f"
done

# --- service secrets (dev.env) ---
ensure_key "$DEV_ENV" ERP_DB_SUPERUSER_PASSWORD rand_hex 24
ensure_key "$DEV_ENV" ERP_DB_OWNER_PASSWORD rand_hex 24
ensure_key "$DEV_ENV" ERP_DB_APP_PASSWORD rand_hex 24
ensure_key "$DEV_ENV" ERP_DB_READONLY_PASSWORD rand_hex 24
ensure_key "$DEV_ENV" ERP_DB_PLATFORM_PASSWORD rand_hex 24
ensure_key "$DEV_ENV" ERP_VALKEY_PASSWORD rand_hex 24
ensure_key "$DEV_ENV" ERP_SECRET_KEY rand_hex 48
ensure_key "$DEV_ENV" ERP_KEYCLOAK_ADMIN_PASSWORD rand_hex 24
ensure_key "$DEV_ENV" ERP_MINIO_ROOT_PASSWORD rand_hex 24
ensure_key "$DEV_ENV" ERP_MCP_CLIENT_SECRET rand_hex 32
ensure_key "$DEV_ENV" ERP_SMOKE_CLIENT_SECRET rand_hex 32
ensure_key "$DEV_ENV" ERP_API_OIDC_CLIENT_SECRET rand_hex 32
ensure_key "$DEV_ENV" ERP_MAILPIT_UI_PASSWORD rand_hex 16
ensure_key "$DEV_ENV" ERP_GRAFANA_ADMIN_PASSWORD rand_hex 16

# --- field encryption key (its own file; never overwritten) ---
KEY_FILE="$ERP_SECRETS_DIR/field-encryption.key"
if [[ ! -s "$KEY_FILE" ]]; then
  legacy="$(sed -n 's/^ERP_FIELD_ENCRYPTION_KEY=//p' "$DEV_ENV" | tail -1)"
  if [[ -n "$legacy" ]]; then printf '%s\n' "$legacy" > "$KEY_FILE"; else rand_b64key > "$KEY_FILE"; fi
  created+=("$KEY_FILE")
fi
chmod 600 "$KEY_FILE"

# --- demo user passwords (dev-credentials.env); usernames per D-P21 / D-018 ---
for user in admin.demo contador.demo vendedor.demo auditor.demo proveedor.demo cliente.demo empleado.demo; do
  key="ERP_DEMO_PASSWORD_$(printf '%s' "$user" | tr '[:lower:].' '[:upper:]_')"
  ensure_key "$CRED_ENV" "$key" rand_hex 12
done

chmod 600 "$DEV_ENV" "$CRED_ENV"
for f in "${created[@]:-}"; do [[ -n "$f" ]] && printf 'created %s (mode 0600)\n' "$f"; done
printf 'secrets: %s\n' "$DEV_ENV"
printf 'demo credentials: %s\n' "$CRED_ENV"
printf 'field encryption key: %s\n' "$KEY_FILE"
