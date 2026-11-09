#!/usr/bin/env bash
# Run the P03 DevOps test suite (T-P03-01..06 + lint/secret tests). Owner: P03.
# JUnit XML -> reports/junit/p03-devops.xml when pytest is available (uv run / venv), else plain unittest.
set -euo pipefail
# shellcheck source=scripts/ci/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
ensure_docker
cd "$NEXUS_REPO_ROOT"
mkdir -p reports/junit
if python3 -c "import pytest" 2>/dev/null; then
  exec python3 -m pytest -q -p no:cacheprovider --rootdir scripts/ci/tests scripts/ci/tests \
    --junitxml=reports/junit/p03-devops.xml "$@"
elif command -v uv >/dev/null 2>&1; then
  exec uv run --no-project --with pytest==8.4.2 python -m pytest -q -p no:cacheprovider --rootdir scripts/ci/tests \
    scripts/ci/tests --junitxml=reports/junit/p03-devops.xml "$@"
else
  cd scripts/ci/tests && exec python3 -m unittest discover -v -p 'test_*.py'
fi
