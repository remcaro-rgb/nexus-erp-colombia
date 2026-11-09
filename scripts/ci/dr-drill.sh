#!/usr/bin/env bash
# make dr-drill (target requested from P01 via CCR P03-1): restore the DEV database into a fresh container and run
# the invariants. Owner: P03. Arguments are passed to scripts/ci/dr_drill.py.
set -euo pipefail
# shellcheck source=scripts/ci/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
ensure_docker
exec python3 "$NEXUS_REPO_ROOT/scripts/ci/dr_drill.py" "$@"
