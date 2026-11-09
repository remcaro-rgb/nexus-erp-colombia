# IAM (P04) — access model

DEV-only edition. The prompt asks for `docs/security/access-model.md`; that path is outside P04's owned paths, so the
access model lives here (copy it there when integrating).

```
 Keycloak realm erp-dev (deploy/keycloak/realm-template.json)
   clients: erp-web (PKCE) · erp-portal (PKCE) · erp-api (audience) · erp-mcp (token exchange) · erp-smoke
        │  RS256 access token: iss, sub, aud∋erp-api, azp, tenant_id, groups, (act)
        ▼
 iam.oidc.verifier ── iss/aud/exp/nbf/typ/azp/tenant checks (JWKS cached 300 s)
        ├── iam.authn.InternalAuthentication  internal clients + INTERNAL users only; X-Company-Id ∈ memberships
        └── iam.authn.PortalAuthentication    erp-portal + EXTERNAL user + ACTIVE external account
        ▼
 TenantContext(tenant, {company}, principal, type, on_behalf_of, channel) ── kernel.tenancy.bind → SET LOCAL app.*
        ▼
 iam.policy (single path, ADR-004)                  PostgreSQL RLS (second line, ADR-003)
   authorize / require      deny by default          ENABLE + FORCE on every iam table
   scope_queryset           company + scopes + rows  tenant_id = app.tenant_id [AND company_id = ANY(app.company_ids)]
   field_rules / field_mask classification + masks  unbound session → 0 rows
   secure_rows              exports, reports, AI, MCP
```

## Principals
| Kind | Permissions |
|---|---|
| Internal user | union over active, currently valid role assignments in the bound company; deny wins inside a role |
| AI agent / MCP client (`act` claim) | the delegating user's permissions ∩ `*.*.read` (delegated write grants: not in this edition) |
| External (portal) user | never an internal permission; only `portal.me.read`, `portal.context.read`, `portal.<kind>_*.*`, rows of its own party/employee and company |
| Service | nothing (service accounts with roles: not in this edition) |

## Scopes (`RoleAssignment.scope`)
`branch_ids`, `warehouse_ids`, `cost_center_ids` (subtree roots, expanded through `kernel.interfaces.Dimensions`; no
provider → nothing). A scope restricts resources that carry the attribute (`branch_id`, `warehouse_id`,
`cost_center_id`; models may remap with `IAM_SCOPE_FIELDS`, e.g. `Branch` → `id`).

## Row policies (`iam_row_policy.rule`)
`{"resource": "sales.invoice", "effect": "allow"|"deny", "actions": ["read"], "where": {"all": [{"attr": "salesperson_id",
"op": "eq", "value": "$principal.user_id"}]}}` — ops `eq ne in not_in lt lte gt gte isnull`; compiled to `Q` for querysets and
evaluated in memory for single resources; deny overrides allow; when allow rules exist, a row must match one.

## Field security
Modules register field classifications (`iam.policy.fields.register("iam.branch", {...})`, from `x-classification`).
A role reads fields up to its `max_classification` (`secret` never); `iam_field_policy` overrides per role/field:
unreadable → omitted (not null); readable with `mask` (`full`, `last4`, `hash`, `range`) → masked; filter/sort/search on an
unreadable or masked field → `400 filter_not_allowed`; writes to non-writable fields → `403 field_forbidden`.

## Using it from another module
```python
from iam.api.base import SecuredView  # DRF base: auth, context, permission per method, masks, pagination, idempotency
from iam import policy  # authorize / require / scope_queryset / field_mask / secure_rows
from iam.external.portal import PortalCollectionView  # portal endpoints (party scope from the token)
from iam.branches.services import validate_branch_link, assert_postable_branch, register_reference, BranchReference
from iam.branches.db import branch_fk  # composite FK (tenant, company, branch) → iam_branch
from iam import rls  # rls.enable("my_table") in your migration
from iam.testing.fakes import AllowAllRecordingPolicy  # unit tests only; security tests use the real engine
```
