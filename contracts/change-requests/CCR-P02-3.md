# CCR-P02-3 — CI and compose wiring of the contract freeze (to P03, owner of CI/Makefile/compose; P01 for the Makefile if it owns it)

Status: OPEN (P02, wave 1)

1. `make ci` must run `pytest tests/contract` (no services; ~30 s) and `python3 contracts/tools/build_contracts.py --check` (stale generated contracts fail).
2. From W1, each merged backend module: generate its schema (drf-spectacular) and run
   `python3 contracts/tools/drift.py --implemented <schema> --modules <module,...>` (exit 1 on drift: missing/undocumented operations,
   missing Idempotency-Key/If-Match, permission mismatch, removed or retyped properties).
3. Dev compose: include `contracts/mock/compose.yaml` (Prism 5.15.10 mock on 127.0.0.1:4010) behind a profile `contract-mock` so P08/P20 can work before the backend.
4. Optional lint: `npx @stoplight/spectral-cli@6.17.1 lint contracts/openapi/openapi.yaml --ruleset contracts/.spectral.yaml` (same rules as test_conventions.py).
