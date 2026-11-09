#!/usr/bin/env bash
# Optional helper mirroring COMMANDS §H5 / make dev-health (D-P03). Owner: P03.
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/scripts/ci/dev-health.sh" "$@"
