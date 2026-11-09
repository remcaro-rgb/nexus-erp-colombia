#!/usr/bin/env bash
# DEV database restore (D-P03): replaces database `erp` of the running DEV stack with a pg_dump -Fc file.
# Owner: P03. DESTRUCTIVE for the current DEV data -> explicit confirmation required:
#   deploy/dev/restore.sh FILE            asks you to type: RESTORE erp
#   deploy/dev/restore.sh FILE --yes      non-interactive (CI / drills only)
# Application services are stopped during the restore and started again afterwards. Volumes are never removed.
set -euo pipefail
# shellcheck source=scripts/ci/lib.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/scripts/ci/lib.sh"

file="${1:-}"; confirm="${2:-}"
[[ -n "$file" && -f "$file" ]] || die "usage: restore.sh BACKUP_FILE [--yes]" 2
if [[ "$confirm" != "--yes" ]]; then
  [[ -t 0 ]] || die "refusing to restore without confirmation (pass --yes in non-interactive use)" 2
  read -r -p "This replaces ALL data of database 'erp' in the DEV stack. Type 'RESTORE erp' to continue: " answer
  [[ "$answer" == "RESTORE erp" ]] || die "not confirmed; nothing changed" 1
fi

ensure_docker
load_runtime_env
compose_profiles_env
mapfile -t apps < <(app_services)
if [[ ${#apps[@]} -gt 0 ]]; then dc stop "${apps[@]}" >&2; fi
dc exec -T db psql -X -v ON_ERROR_STOP=1 -U postgres -d postgres >&2 <<'SQL'
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'erp' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS erp;
CREATE DATABASE erp OWNER erp_owner ENCODING 'UTF8' TEMPLATE template0;
REVOKE ALL ON DATABASE erp FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE erp TO erp_app, erp_readonly, erp_platform;
SQL
dc exec -T db pg_restore -U postgres -d erp --exit-on-error --single-transaction < "$file"
if [[ ${#apps[@]} -gt 0 ]]; then dc up -d --wait "${apps[@]}" >&2; fi
printf 'restored %s into database erp\n' "$file"
