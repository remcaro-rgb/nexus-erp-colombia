# contracts/ — frozen API, event, permission, error and import/export contracts (P02, contracts-v1.0.0, DEV-only)

| Path | What | Edited by |
|---|---|---|
| `src/root.yaml` | shared OpenAPI components: Money, Quantity, Rate, Percent, DateRange, Nit, PartyRef, DocumentRef, AuditStamp, DimensionSlots, Page, Problem, Operation, SimulationResult, Bulk*, parameters (Idempotency-Key, If-Match, X-Company-Id, fields, filter, sort, cursor, limit ≤ 500), security (OAuth2 code+PKCE, client credentials, RFC 8693 token exchange) | P02 |
| `src/modules/<module>.yaml` | compact module source: resources, actions, fields + classifications, errors, events, import/export entities, feature tags | module owner after G0 (additive) |
| `src/roles.yaml` | 12 default role templates | P02 / P04 |
| `src/registry-coverage.yaml` | manual coverage for FEATURES-ADDENDUM API lines without endpoint tokens | P02 |
| `errors.yaml` | stable error codes (RFC 9457 `code`) | P02 (append by CCR) |
| `openapi/root.yaml`, `openapi/modules/*.yaml` | generated OpenAPI 3.1 root + one fragment per module (`$ref: ../root.yaml#/…`) | generated |
| `openapi/openapi.yaml` / `.json` | generated bundle (all paths + event webhooks) — used by tests, drift detector and mock server | generated |
| `events/envelope.v1.json`, `events/<module>/<type>.json`, `events/<module>/examples/` | JSON Schema 2020-12 per event (envelope inlined) + valid synthetic example | generated |
| `permissions/catalog.yaml` | permission codes `<module>.<resource>.<action>` ↔ operations, role templates, classification list | generated + union-merged appends |
| `dataio/<entity>.yaml` | import/export specs (columns, types, required, natural keys, lookups, rules) — 46 entities | generated |
| `interfaces/kernel_interfaces_draft.py` | typed Protocol drafts for `backend/kernel/interfaces` (CCR-P02-2) | P02 → P01 |
| `tools/build_contracts.py` | generator (`--check` = drift between sources and generated files) | P02 |
| `tools/registry_diff.py` | T-X-QA-06 diff: contract vs feature registry + FEATURES-ADDENDUM | P02 |
| `tools/drift.py` | implemented schema (Django/drf-spectacular) vs contract | P02 |
| `.spectral.yaml` | Spectral ruleset (same rules as `tests/contract/test_conventions.py`) | P02 |
| `mock/compose.yaml` | Prism mock server on 127.0.0.1:4010 | P02 |
| `VERSIONING.md` | SemVer, deprecation (`Deprecation`, `Sunset`), v2 paths/events by CCR | P02 |

```bash
python3 contracts/tools/build_contracts.py           # regenerate after editing contracts/src or errors.yaml
python3 contracts/tools/build_contracts.py --check   # CONTRACTS-OK / CONTRACTS-DRIFT
python3 contracts/tools/registry_diff.py             # REGISTRY-DIFF-OK
pytest tests/contract                                # all contract tests (no services)
docker compose -f contracts/mock/compose.yaml up -d  # mock API on http://127.0.0.1:4010
```

Conventions: resources `/api/v1/<module>/<resource>`; actions `POST …/{id}:<verb>` (collection actions `POST …:<verb>`); every state change is an action
with a required `Idempotency-Key`; PATCH/PUT need `If-Match` and never change `status`; long-running → `202` + `/api/v1/operations/{id}`; `:bulk` ≤ 1,000 items;
`:simulate` returns documents + journal entries + stock moves without persisting; decimals are strings; every operation has `x-permission`, `x-error-codes`,
`x-owner` and (for FEATURES-ADDENDUM features) `x-features`; every property has `x-classification`.
