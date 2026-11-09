# DEP-P02-1 — test dependencies for `tests/contract` (to P01, owner of pyproject.toml / uv.lock)

Status: OPEN (P02, wave 1)

`pytest tests/contract` (contract freeze tests incl. mandatory **T-X-QA-06**) needs, in the dev dependency group:

| package | version (exact pin) | license | reason |
|---|---|---|---|
| pytest | 8.4.2 (or the project's pytest pin) | MIT | test runner |
| PyYAML | 6.0.2 | MIT | read contracts/*.yaml; generator `contracts/tools/build_contracts.py` |
| jsonschema | 4.25.1 | MIT | OpenAPI 3.1 meta-schema validation (vendored schema in contracts/tools/schemas), event JSON Schema 2020-12 validation |

No network and no services are needed. The tests were verified with exactly these versions on Python 3.13 (P02 handoff).
