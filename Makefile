# Nexus ERP Colombia — root Makefile (owner P01). DEV-only.
# Every target is non-interactive and returns non-zero on failure.

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

UV      ?= uv
PY      := $(UV) run --frozen python
PYTEST  := $(UV) run --frozen pytest
RUFF    := $(UV) run --frozen ruff
MYPY    := $(UV) run --frozen mypy
MANAGE  := cd backend && $(UV) run --frozen python manage.py
DB_USER ?= $(shell id -un)
IDS     ?=

.PHONY: help setup db-setup dev lint typecheck test unit contract frontend-test ci \
        test-ids e2e migrations-check seed-demo scenarios \
        dev-up dev-down dev-seed dev-health reports

help:
	@echo "targets: setup db-setup dev lint typecheck test ci test-ids IDS=\"T-..\" e2e"
	@echo "         migrations-check seed-demo scenarios dev-up dev-down dev-seed dev-health"

reports:
	@mkdir -p reports/junit reports/playwright reports/coverage

setup:
	$(UV) sync --frozen

# Local PostgreSQL role for the OS user (peer auth over the unix socket, no
# password): LOGIN CREATEDB, never SUPERUSER/BYPASSRLS. Skipped when
# DATABASE_URL is set.
db-setup:
	@cd backend && $(UV) run --frozen python -m kernel.devdb "$(DB_USER)"

dev: db-setup
	$(MANAGE) migrate --noinput
	$(MANAGE) runserver 127.0.0.1:$${ERP_DEV_PORT:-8080}

lint:
	$(RUFF) check backend
	cd backend && $(UV) run --frozen python -m kernel.lint.no_float .

typecheck:
	$(MYPY) backend/kernel

migrations-check: db-setup
	$(MANAGE) makemigrations --check --dry-run

unit: reports db-setup
	$(PYTEST) backend --junitxml=reports/junit/ci-backend.xml \
	  --cov --cov-report=term --cov-report=xml:reports/coverage/backend.xml

test: unit

contract: reports
	@if [ -d tests/contract ]; then \
	  set +e; $(PYTEST) tests/contract --junitxml=reports/junit/ci-contract.xml; rc=$$?; set -e; \
	  if [ $$rc -ne 0 ] && [ $$rc -ne 5 ]; then exit $$rc; fi; \
	else echo "tests/contract absent; skipping"; fi

frontend-test:
	@if [ -f package.json ]; then pnpm -r test; else echo "package.json absent; skipping"; fi

ci: lint typecheck migrations-check unit contract frontend-test
	@echo "make ci: OK"

# IDS="T-INV-LOC-02 T-A-01" -> pytest -k "T_INV_LOC_02 or T_A_01"; vitest -t; playwright --grep.
test-ids: reports db-setup
	@if [ -z "$(strip $(IDS))" ]; then echo "usage: make test-ids IDS=\"T-..\""; exit 2; fi
	@K="$$(echo '$(strip $(IDS))' | tr -- '-' '_' | sed 's/ \+/ or /g')"; \
	 PATHS="backend"; if [ -d tests ]; then PATHS="$$PATHS tests"; fi; \
	 set +e; $(PYTEST) $$PATHS -k "$$K" --ignore=tests/e2e \
	   --junitxml=reports/junit/test-ids-backend.xml; rc=$$?; set -e; \
	 if [ $$rc -ne 0 ] && [ $$rc -ne 5 ]; then exit $$rc; fi
	@if [ -f package.json ] && [ -d frontend ]; then \
	  PAT="$$(echo '$(strip $(IDS))' | sed 's/ \+/|/g')"; \
	  pnpm --dir frontend exec vitest run -t "$$PAT" --reporter=default --reporter=junit \
	    --outputFile.junit=../reports/junit/test-ids-frontend.xml --passWithNoTests; \
	  PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 PLAYWRIGHT_JSON_OUTPUT_NAME=$(CURDIR)/reports/playwright/results.json \
	    pnpm --dir frontend exec playwright test --grep "$$PAT" --reporter=json --pass-with-no-tests; \
	else echo "frontend absent; skipping vitest/playwright"; fi

e2e: reports
	@if [ ! -f package.json ] || [ ! -d frontend ]; then echo "not available yet"; exit 2; fi
	@if [ -z "$${CHROME_PATH:-}" ]; then echo "CHROME_PATH not set (system Chrome required)"; exit 2; fi
	PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 PLAYWRIGHT_JSON_OUTPUT_NAME=$(CURDIR)/reports/playwright/results.json \
	  pnpm --dir frontend exec playwright test --reporter=list,json

seed-demo: db-setup
	$(MANAGE) seed_demo

scenarios: reports db-setup
	@if [ -d tests/cross_module ]; then \
	  $(PYTEST) tests/cross_module --junitxml=reports/junit/scenarios.xml; \
	else echo "not available yet"; exit 2; fi

dev-up:
	@if [ -f scripts/ci/dev-up.sh ]; then bash scripts/ci/dev-up.sh; else echo "not available yet"; exit 2; fi

dev-down:
	@if [ -f scripts/ci/dev-down.sh ]; then bash scripts/ci/dev-down.sh; else echo "not available yet"; exit 2; fi

dev-seed:
	@if [ -f scripts/ci/dev-seed.sh ]; then bash scripts/ci/dev-seed.sh; else echo "not available yet"; exit 2; fi

dev-health:
	@if [ -f scripts/ci/dev-health.sh ]; then bash scripts/ci/dev-health.sh; else echo "not available yet"; exit 2; fi
