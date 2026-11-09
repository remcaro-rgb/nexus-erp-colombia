# Contract versioning policy (contracts-v1.0.0)

Applies to everything under `contracts/`: OpenAPI (`openapi/`), events (`events/`), permissions (`permissions/catalog.yaml`), error codes (`errors.yaml`),
import/export specs (`dataio/`) and interface drafts (`interfaces/`).

## Versions
* **SemVer** for the contract set: `MAJOR.MINOR.PATCH` in `info.version` of `openapi/root.yaml` and every fragment, and `version` of `errors.yaml`,
  `permissions/catalog.yaml`, `events/catalog.yaml`. G0 freezes **1.0.0** (the platform refuses git tags, so the freeze is recorded by the integrator in
  `docs/run/STATE.json` and the branch `release/rc-*`, see CLOUD-ADDENDUM).
* **PATCH**: descriptions, examples, typo fixes — no shape change.
* **MINOR (additive)**: new operation, new optional request property, new response property, new optional query parameter, new error code, new event type,
  new enum value in a response-only enum, new permission code, new import column that is optional. Module owners may make additive changes in their own
  fragment source (`contracts/src/modules/<module>.yaml`) after G0 without a CCR; `make ci` contract tests must stay green.
* **MAJOR (breaking)**: removing/renaming an operation, property, parameter, enum value, error code, permission code or event; changing a type or
  making something required; changing semantics. Breaking changes need an approved **CCR** (`contracts/change-requests/CCR-<agent>-<n>.md`) and a new
  version path: `/api/v2/<module>/…` for HTTP, `<module>.<entity>.<verb>.v2` for events (a new JSON Schema file next to `.v1`). The `v1` surface keeps working
  until its sunset.

## Deprecation
* A deprecated operation keeps responding and adds the response headers **`Deprecation`** (RFC 9745, date it was deprecated) and **`Sunset`** (RFC 8594,
  earliest removal date, at least two minor releases or 90 days later) plus `Link: <…>; rel="successor-version"`. The operation gets `deprecated: true`.
* Deprecated events are published in both versions until the sunset date; consumers dedupe by `event_id`.
* Error codes and permission codes are **never reused** for a different meaning.

## Mechanics
* Sources live in `contracts/src/`; generated files are produced by `python3 contracts/tools/build_contracts.py` and committed. `tests/contract` fails when
  generated files are stale (`build_contracts.py --check`).
* Drift between the backend and the contract is detected by `contracts/tools/drift.py` (implemented schema vs `openapi/openapi.json`); from W1 CI runs it
  per merged module.
* `contracts/permissions/catalog.yaml` is union-merged: later tasks append their own codes with `source: <task>`; the generator keeps them.
