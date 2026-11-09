"""P22 QA harness (milestone P22a): shared, dependency-light building blocks for the
cross-module, security, perf and e2e suites.

Modules:
* ``paths``             repository paths and loaders (contracts, registry).
* ``openapi``           index over the frozen OpenAPI bundle (operations, channels, effects, field classes).
* ``principals``        2 tenants x 3 companies x 7 principal kinds, permission evaluation from role templates.
* ``oracle``            the expected policy: allowed actions, visible rows, readable fields.
* ``leak``              invariant-12 leak framework: case generation, transports, checker, matrix.
* ``failure_injection`` fail-after-N-SQL-statements injector + all-or-nothing atomicity probe.
* ``routes``            implemented Django routes vs the contract (UR-22 conformance).
* ``ledger_dump``       scenario ledger CSV dumps for the contador review (EG-8).
* ``traceability``      ``docs/traceability.csv`` generator from ``@pytest.mark.req`` / Playwright tags.
* ``evidence``          gate evidence collector into ``docs/gates/G<N>-evidence/``.

Only the standard library, PyYAML and (optionally) Django are imported at module level.
"""

HARNESS_VERSION = "1.0.0"
