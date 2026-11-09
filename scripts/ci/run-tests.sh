#!/usr/bin/env bash
# Run the P03 DevOps test suite (T-P03-01..06 + lint/secret tests). Owner: P03.
# JUnit XML -> reports/junit/p03-devops.xml. Runs in the uv.lock environment incl. the dev group (uv sync --frozen);
# plain unittest only when neither uv nor a complete pytest environment exists.
set -euo pipefail
# shellcheck source=scripts/ci/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
ensure_docker
cd "$NEXUS_REPO_ROOT"
mkdir -p reports/junit
# The repository pytest configuration (pyproject.toml) loads pytest-django and -p kernel.testing.pytest_plugin
# (hypothesis): the tests must run in the locked environment *with the dev dependency group* (FIX-D-W01-1, CI-2).
# pytest-django imports the Django settings, which need a database source: the same local unix-socket default
# as the Makefile when neither DATABASE_URL nor ERP_DB_* is set (P03 tests do not connect to it).
if [[ -z "${DATABASE_URL:-}${ERP_DB_HOST:-}${ERP_DB_NAME:-}${ERP_DB_USER:-}" ]]; then
  export DATABASE_URL="postgres:///nexus_erp?host=/var/run/postgresql"
fi
PYTEST_ARGS=(-q -p no:cacheprovider --rootdir scripts/ci/tests scripts/ci/tests --junitxml=reports/junit/p03-devops.xml)
if command -v uv >/dev/null 2>&1 && [[ -f uv.lock ]]; then
  uv sync --frozen --quiet
  exec uv run --frozen python -m pytest "${PYTEST_ARGS[@]}" "$@"
elif python3 -c "import pytest, pytest_django, hypothesis" 2>/dev/null; then
  exec python3 -m pytest "${PYTEST_ARGS[@]}" "$@"
else
  cd scripts/ci/tests && exec python3 -m unittest discover -v -p 'test_*.py'
fi
