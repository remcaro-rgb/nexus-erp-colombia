"""Policy engine: the ``kernel.interfaces.PolicyService`` implementation (ADR-004).

Deny by default. The effective permissions of a principal are computed from its
role assignments (active, valid on the business date, company in the context),
row policies and field policies, and cached under
``(tenant, principal, on_behalf_of, companies, policy_version, business date)``.
Every change to roles, assignments, policies or accounts bumps the tenant's
``policy_version`` in the same transaction, so a revocation takes effect on the
very next request (no cached allow survives it).

Scopes (``RoleAssignment.scope``): ``branch_ids``, ``warehouse_ids`` and
``cost_center_ids``. Cost-center ids are subtree roots expanded through
``kernel.interfaces.Dimensions`` (P05); when that service is unavailable the
scope expands to nothing (fail closed).

Principal kinds:
* internal users — union of their assignments' grants (deny wins inside a role);
* AI agent / MCP client — the delegating user's grants restricted to ``*.*.read``
  (delegated grants with write scopes are not part of this edition, see handoff);
* external (portal) users — never any internal permission; only ``portal.me.read``,
  ``portal.context.read`` and ``portal.<kind>_*.*`` codes;
* service principals — nothing (deny) until service accounts carry roles.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import structlog
from django.core.cache import cache
from django.db.models import F, Q

from iam.errors import PolicyDenied
from iam.policy import fields as field_registry
from iam.policy import rules
from iam.policy.patterns import classification_rank, matches, role_allows
from kernel import clock, services, tenancy
from kernel.dates import effective_q
from kernel.errors import DomainError
from kernel.interfaces.dimensions import Dimensions
from kernel.interfaces.policy import Decision, FieldMask
from kernel.tenancy import PrincipalType, TenantContext

SCOPE_KEYS = ("branch_ids", "warehouse_ids", "cost_center_ids")
SCOPE_ATTR = {"branch_ids": "branch_id", "warehouse_ids": "warehouse_id", "cost_center_ids": "cost_center_id"}
READ_ONLY_DELEGATION = ("*.*.read",)
CACHE_SECONDS = 300
log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Grant:
    company_id: uuid.UUID
    role_id: uuid.UUID
    role_key: str
    grants: tuple[str, ...]
    denies: tuple[str, ...]
    max_classification: str
    scopes: tuple[tuple[str, frozenset[uuid.UUID]], ...] = ()

    def allows(self, code: str) -> bool:
        return role_allows(self.grants, self.denies, code)

    def scope(self, key: str) -> frozenset[uuid.UUID] | None:
        for k, ids in self.scopes:
            if k == key:
                return ids
        return None


@dataclass(frozen=True, slots=True)
class PortalIdentity:
    account_id: uuid.UUID
    kind: str
    company_id: uuid.UUID
    party_id: uuid.UUID | None
    employee_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class Effective:
    principal_kind: str  # internal | delegated | external | none
    grants: tuple[Grant, ...] = ()
    row_policies: tuple[tuple[uuid.UUID | None, str, dict[str, Any]], ...] = ()
    field_policies: tuple[tuple[uuid.UUID | None, str, str, bool, bool, str], ...] = ()
    portal: PortalIdentity | None = None
    attrs: dict[str, Any] = field(default_factory=dict)

    def allowing(self, code: str) -> list[Grant]:
        if self.principal_kind == "delegated" and not matches(READ_ONLY_DELEGATION, code):
            return []
        return [g for g in self.grants if g.allows(code)]

    def permissions(self, catalog: Iterable[str]) -> list[str]:
        return sorted(c for c in catalog if self.allowing(c))


def _resource_of(action: str) -> str:
    parts = action.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else action


def _get(obj: Any, name: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


def _as_uuid(value: Any) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


class PolicyEngine:
    """Process-wide engine. Stateless apart from the shared Django cache."""

    # ------------------------------------------------------------ version / cache
    def policy_version(self, ctx: TenantContext) -> int:
        from iam.models import PolicyVersion

        with tenancy.scope(ctx):
            row = PolicyVersion.objects.filter(tenant_id=ctx.tenant_id).values_list("version", flat=True).first()
        return int(row or 1)

    @staticmethod
    def bump_version(tenant_id: uuid.UUID) -> int:
        """Increment the tenant policy version (call inside the changing UoW; row-locked, no lost updates)."""
        from iam.models import PolicyVersion

        row, created = PolicyVersion.objects.select_for_update().get_or_create(
            tenant_id=tenant_id, defaults={"version": 2}
        )
        if not created:
            PolicyVersion.objects.filter(tenant_id=tenant_id).update(version=F("version") + 1)
            row.refresh_from_db(fields=["version"])
        return int(row.version)

    def effective(self, ctx: TenantContext) -> Effective:
        version = self.policy_version(ctx)
        today = clock.business_date()
        companies = ",".join(sorted(str(c) for c in ctx.company_ids))
        raw = (
            f"{ctx.tenant_id}|{ctx.principal_id}|{ctx.principal_type}|{ctx.on_behalf_of}|{companies}|{version}|{today}"
        )
        key = "iam:eff:" + hashlib.sha256(raw.encode()).hexdigest()
        hit = cache.get(key)
        if isinstance(hit, Effective):
            return hit
        eff = self._compute(ctx, today)
        cache.set(key, eff, CACHE_SECONDS)
        return eff

    # ------------------------------------------------------------ computation
    def _compute(self, ctx: TenantContext, today: dt.date) -> Effective:
        from iam.models import ExternalAccount, FieldPolicy, RoleAssignment, RowPolicy, User

        if ctx.principal_type == PrincipalType.SERVICE:
            return Effective("none")
        delegated = ctx.principal_type in (PrincipalType.AI_AGENT, PrincipalType.MCP_CLIENT)
        user_id = ctx.on_behalf_of if delegated else ctx.principal_id
        with tenancy.scope(ctx):
            user = User.objects.filter(tenant_id=ctx.tenant_id, id=user_id).first()
            if user is None or user.status != User.Status.ACTIVE:
                return Effective("none")
            if user.kind == User.Kind.EXTERNAL:
                if delegated:
                    return Effective("none")  # AI/MCP not available to external users (F-PORTAL-CORE)
                acc = ExternalAccount.objects.filter(tenant_id=ctx.tenant_id, user_id=user.id).first()
                if acc is None or acc.external_status != ExternalAccount.ExternalStatus.ACTIVE:
                    return Effective("none")
                if acc.company_id not in ctx.company_ids:
                    return Effective("none")
                portal = PortalIdentity(acc.id, acc.portal_kind, acc.company_id, acc.party_id, acc.employee_id)
                return Effective("external", portal=portal, attrs={"user_id": user.id})
            assignments = list(
                RoleAssignment.objects.filter(
                    tenant_id=ctx.tenant_id,
                    user_id=user.id,
                    active=True,
                    role__active=True,
                    company_id__in=list(ctx.company_ids),
                )
                .filter(effective_q(today))
                .select_related("role")
            )
            role_ids = {a.role_id for a in assignments}
            rp = tuple(
                (p.role_id, p.resource, dict(p.rule))
                for p in RowPolicy.objects.filter(tenant_id=ctx.tenant_id, active=True).filter(
                    Q(role_id__isnull=True) | Q(role_id__in=role_ids)
                )
            )
            fp = tuple(
                (p.role_id, p.resource, p.field, p.readable, p.writable, p.mask)
                for p in FieldPolicy.objects.filter(tenant_id=ctx.tenant_id, active=True).filter(
                    Q(role_id__isnull=True) | Q(role_id__in=role_ids)
                )
            )
        grants = tuple(self._grant(a, today) for a in assignments)
        attrs = {
            "user_id": user.id,
            "company_ids": sorted(ctx.company_ids),
            "principal_id": ctx.principal_id,
        }
        return Effective("delegated" if delegated else "internal", grants, rp, fp, None, attrs)

    def _grant(self, assignment: Any, today: dt.date) -> Grant:
        scope = assignment.scope or {}
        scopes: list[tuple[str, frozenset[uuid.UUID]]] = []
        for key in SCOPE_KEYS:
            if key not in scope or scope[key] is None:
                continue
            ids = {u for u in (_as_uuid(v) for v in scope[key]) if u is not None}
            if key == "cost_center_ids":
                ids = self._expand_subtrees(assignment.company_id, ids, today)
            scopes.append((key, frozenset(ids)))
        role = assignment.role
        return Grant(
            assignment.company_id,
            role.id,
            role.key,
            tuple(role.grants or ()),
            tuple(role.denies or ()),
            role.max_classification,
            tuple(scopes),
        )

    @staticmethod
    def _expand_subtrees(company_id: uuid.UUID, roots: set[uuid.UUID], today: dt.date) -> set[uuid.UUID]:
        dims = services.optional(Dimensions)
        if dims is None:
            return set()  # fail closed: no dimension service → the scope grants nothing
        out: set[uuid.UUID] = set()
        for root in roots:
            try:
                out.update(dims.subtree(company_id, "cost_center", root, today))
            except DomainError:
                log.warning("iam.scope.unknown_cost_center", company_id=str(company_id), node_id=str(root))
        return out

    # ------------------------------------------------------------ PolicyService
    def authorize(self, ctx: TenantContext, action: str, resource: Any = None) -> Decision:
        eff = self.effective(ctx)
        if eff.principal_kind == "external":
            return self._authorize_portal(ctx, eff, action, resource)
        if eff.principal_kind == "none":
            return Decision(False, "deny_by_default")
        candidates = eff.allowing(action)
        if not candidates:
            return Decision(False, "deny_by_default")
        if resource is None:
            return Decision(True, "granted")
        tenant = _as_uuid(_get(resource, "tenant_id"))
        if tenant is not None and tenant != ctx.tenant_id:
            return Decision(False, "tenant_mismatch")
        company = _as_uuid(_get(resource, "company_id"))
        if company is not None:
            if company not in ctx.company_ids:
                return Decision(False, "company_not_allowed")
            candidates = [g for g in candidates if g.company_id == company]
        candidates = [g for g in candidates if self._in_scope(g, resource)]
        if not candidates:
            return Decision(False, "out_of_scope")
        resource_type = _resource_of(action)
        verb = action.rsplit(".", 1)[-1]
        if not self._row_rules_allow(eff, candidates, resource_type, verb, resource):
            return Decision(False, "row_policy")
        return Decision(True, "granted")

    def require(self, ctx: TenantContext, action: str, resource: Any = None) -> None:
        decision = self.authorize(ctx, action, resource)
        if not decision.allowed:
            raise PolicyDenied(f"not allowed: {action}")

    def _authorize_portal(self, ctx: TenantContext, eff: Effective, action: str, resource: Any) -> Decision:
        portal = eff.portal
        if portal is None:
            return Decision(False, "deny_by_default")
        allowed = action in ("portal.me.read", "portal.context.read") or matches((f"portal.{portal.kind}_*.*",), action)
        if not allowed:
            return Decision(False, "external_principal")
        if resource is not None and not self.portal_owns(eff, resource):
            return Decision(False, "external_account_scope_violation")
        return Decision(True, "portal")

    @staticmethod
    def portal_owns(eff: Effective, resource: Any) -> bool:
        portal = eff.portal
        if portal is None:
            return False
        company = _as_uuid(_get(resource, "company_id"))
        if company != portal.company_id:
            return False
        if portal.kind == "employee":
            return _as_uuid(_get(resource, "employee_id")) == portal.employee_id
        return _as_uuid(_get(resource, "party_id")) == portal.party_id

    @staticmethod
    def _in_scope(grant: Grant, resource: Any) -> bool:
        """A scope restricts only resources that carry the scoped attribute."""
        if isinstance(resource, Mapping):
            overrides: Mapping[str, str] = resource.get("__scope_fields__", {})
        else:
            overrides = getattr(type(resource), "IAM_SCOPE_FIELDS", {})
        for key, ids in grant.scopes:
            attr = overrides.get(key, SCOPE_ATTR[key])
            present = attr in resource if isinstance(resource, Mapping) else hasattr(resource, attr)
            if present and _as_uuid(_get(resource, attr)) not in ids:
                return False
        return True

    def _row_rules_allow(
        self, eff: Effective, grants: list[Grant], resource_type: str, verb: str, resource: Any
    ) -> bool:
        role_ids = {g.role_id for g in grants}
        applicable = [
            rule
            for role_id, res, rule in eff.row_policies
            if res == resource_type and verb in rule.get("actions", []) and (role_id is None or role_id in role_ids)
        ]
        obj = resource if isinstance(resource, Mapping) else _model_dict(resource)
        where_default: dict[str, Any] = {"all": []}
        for rule in applicable:
            if rule.get("effect") == "deny" and rules.evaluate(rule.get("where") or where_default, eff.attrs, obj):
                return False
        allows = [r for r in applicable if r.get("effect") == "allow"]
        if not allows:
            return True
        return any(rules.evaluate(r.get("where") or where_default, eff.attrs, obj) for r in allows)

    def scope_queryset(self, ctx: TenantContext, action: str, queryset: Any) -> Any:
        model = queryset.model
        names = {f.attname for f in model._meta.concrete_fields}
        qs = queryset.filter(tenant_id=ctx.tenant_id) if "tenant_id" in names else queryset.none()
        eff = self.effective(ctx)
        if eff.principal_kind == "external":
            return self._scope_portal(eff, action, qs, names)
        candidates = eff.allowing(action)
        if eff.principal_kind == "none" or not candidates:
            return qs.none()
        company_attr = (
            "company_id" if "company_id" in names else ("id" if model._meta.db_table == "iam_company" else None)
        )
        overrides: dict[str, str] = getattr(model, "IAM_SCOPE_FIELDS", {})
        combined = Q(pk__in=[])
        for g in candidates:
            q = Q(**{company_attr: g.company_id}) if company_attr else Q()
            for key, ids in g.scopes:
                attr = overrides.get(key, SCOPE_ATTR[key])
                if attr in names:
                    q &= Q(**{f"{attr}__in": list(ids)})
            combined |= q
        qs = qs.filter(combined)
        if company_attr:
            qs = qs.filter(**{f"{company_attr}__in": list(ctx.company_ids)})
        resource_type = _resource_of(action)
        verb = action.rsplit(".", 1)[-1]
        role_ids = {g.role_id for g in candidates}
        applicable = [
            rule
            for role_id, res, rule in eff.row_policies
            if res == resource_type and verb in rule.get("actions", []) and (role_id is None or role_id in role_ids)
        ]
        for rule in applicable:
            if rule.get("effect") == "deny":
                qs = qs.exclude(rules.to_q(rule.get("where") or {"all": []}, eff.attrs, names))
        allows = [r for r in applicable if r.get("effect") == "allow"]
        if allows:
            allow_q = Q(pk__in=[])
            for r in allows:
                allow_q |= rules.to_q(r.get("where") or {"all": []}, eff.attrs, names)
            qs = qs.filter(allow_q)
        return qs

    def _scope_portal(self, eff: Effective, action: str, qs: Any, names: set[str]) -> Any:
        portal = eff.portal
        if portal is None or not matches(
            (f"portal.{portal.kind}_*.*", "portal.me.read", "portal.context.read"), action
        ):
            return qs.none()
        if "company_id" not in names:
            return qs.none()
        qs = qs.filter(company_id=portal.company_id)
        if portal.kind == "employee":
            return qs.filter(employee_id=portal.employee_id) if "employee_id" in names else qs.none()
        return qs.filter(party_id=portal.party_id) if "party_id" in names else qs.none()

    # ------------------------------------------------------------ fields
    def field_mask(self, ctx: TenantContext, resource_type: str) -> FieldMask:
        readable, writable, _masks = self.field_rules(ctx, resource_type)
        return FieldMask(frozenset(readable), frozenset(writable))

    def field_rules(self, ctx: TenantContext, resource_type: str) -> tuple[set[str], set[str], dict[str, str]]:
        """(readable, writable, masks) for ``resource_type`` (``<module>.<resource>``)."""
        catalog = field_registry.fields_of(resource_type)
        eff = self.effective(ctx)
        if eff.principal_kind in ("none", "external"):
            return set(), set(), {}
        readers = eff.allowing(f"{resource_type}.read")
        writers = [g for g in eff.grants if g.allows(f"{resource_type}.update") or g.allows(f"{resource_type}.create")]
        if eff.principal_kind == "delegated":
            writers = []
        readable: set[str] = set()
        writable: set[str] = set()
        masks: dict[str, str] = {}
        clear: set[str] = set()
        for g in readers:
            top = classification_rank(g.max_classification)
            for name, cls in catalog.items():
                ok = cls != "secret" and classification_rank(cls) <= top
                mask = "none"
                for role_id, res, fname, can_read, _w, m in eff.field_policies:
                    if res == resource_type and fname == name and role_id in (None, g.role_id):
                        ok, mask = can_read and cls != "secret", m
                        if role_id == g.role_id:
                            break
                if ok:
                    readable.add(name)
                    if mask == "none":
                        clear.add(name)
                    else:
                        masks.setdefault(name, mask)
        for name in clear:
            masks.pop(name, None)
        for g in writers:
            top = classification_rank(g.max_classification)
            for name, cls in catalog.items():
                ok = cls != "secret" and classification_rank(cls) <= top
                for role_id, res, fname, _r, can_write, _m in eff.field_policies:
                    if res == resource_type and fname == name and role_id in (None, g.role_id):
                        ok = can_write and cls != "secret"
                        if role_id == g.role_id:
                            break
                if ok:
                    writable.add(name)
        return readable, writable, masks

    def secure_rows(
        self, ctx: TenantContext, action: str, resource_type: str, rows: Iterable[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        """Row filter + field mask for exports, reports, AI and MCP (same checks as the API)."""
        readable, _w, masks = self.field_rules(ctx, resource_type)
        out: list[dict[str, Any]] = []
        for row in rows:
            if not self.authorize(ctx, action, row).allowed:
                continue
            out.append({k: field_registry.apply_mask(masks.get(k, "none"), v) for k, v in row.items() if k in readable})
        return out


def _model_dict(obj: Any) -> dict[str, Any]:
    meta = getattr(obj, "_meta", None)
    if meta is None:
        return {}
    return {f.attname: getattr(obj, f.attname) for f in meta.concrete_fields}


_ENGINE = PolicyEngine()


def get_engine() -> PolicyEngine:
    return _ENGINE


__all__ = ["Effective", "Grant", "PolicyEngine", "PortalIdentity", "get_engine"]
