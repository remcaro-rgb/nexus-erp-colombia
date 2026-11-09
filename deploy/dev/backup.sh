#!/usr/bin/env bash
# DEV database backup (D-P03): pg_dump -Fc of database `erp` from the running DEV stack. Owner: P03.
# Output: $ERP_BACKUP_DIR (default ~/.local/share/nexus-erp-dev/backups, outside the repo), mode 0600.
# Synthetic data only, but treated as sensitive anyway. Prints the file path, never credentials.
set -euo pipefail
# shellcheck source=scripts/ci/lib.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/scripts/ci/lib.sh"

ensure_docker
load_runtime_env
BACKUP_DIR="${ERP_BACKUP_DIR:-$HOME/.local/share/nexus-erp-dev/backups}"
umask 077
mkdir -p "$BACKUP_DIR"
ts="$(date -u +%Y%m%dT%H%M%SZ)"
out="$BACKUP_DIR/erp-$ts.dump"
dc exec -T db pg_dump -U postgres -Fc -d erp > "$out.partial"
mv "$out.partial" "$out"
chmod 600 "$out"
printf 'backup written: %s (%s bytes)\n' "$out" "$(stat -c %s "$out")"
