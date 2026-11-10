"""Dimension rule matrix (DIM-R08): REQUIRED / ALLOWED / FORBIDDEN per dimension ×
(account, account range, document type, concept, journal, stock document).

Resolution per line and dimension: every matching rule is a candidate; the
highest ``priority`` wins; on a tie the most restrictive mode wins
(FORBIDDEN > REQUIRED > ALLOWED). ``Account.requires_dimensions`` contributes a
REQUIRED candidate with priority 0. No candidate → ALLOWED (optional).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from kernel.errors import ValidationFailed
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork
from ledger.models import DIMENSIONS, DimensionRule, DimensionRuleSet

MODES = ("REQUIRED", "ALLOWED", "FORBIDDEN")
SCOPES = tuple(DimensionRule.ScopeType.values)
_STRICTNESS = {"ALLOWED": 0, "REQUIRED": 1, "FORBIDDEN": 2}


@dataclass(frozen=True, slots=True)
class Rule:
    dimension: str
    scope_type: str
    scope_ref: str
    mode: str
    priority: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "scope_type": self.scope_type,
            "scope_ref": self.scope_ref,
            "mode": self.mode,
            "priority": self.priority,
        }


@dataclass(frozen=True, slots=True)
class LineScope:
    account_code: str
    document_type: str
    concept: str
    journal_code: str


def _account_in_range(code: str, ref: str) -> bool:
    """``"5105-5199"`` (inclusive, lexical on equal-length prefixes) or ``"51*"`` (prefix)."""
    ref = ref.strip()
    if ref.endswith("*"):
        return code.startswith(ref[:-1])
    if "-" in ref:
        low, high = (p.strip() for p in ref.split("-", 1))
        width = max(len(low), len(high))
        head = code[:width]
        return low <= head <= high
    return code.startswith(ref)


def matches(rule: Rule, scope: LineScope) -> bool:
    if rule.scope_type == "account":
        return scope.account_code == rule.scope_ref
    if rule.scope_type == "account_range":
        return _account_in_range(scope.account_code, rule.scope_ref)
    if rule.scope_type == "document_type":
        return scope.document_type == rule.scope_ref
    if rule.scope_type == "stock_document":
        return scope.document_type == rule.scope_ref and scope.document_type.startswith("inventory.")
    if rule.scope_type == "concept":
        return scope.concept == rule.scope_ref
    if rule.scope_type == "journal":
        return scope.journal_code == rule.scope_ref
    return False


def resolve(rules: list[Rule], scope: LineScope, account_requires: list[str]) -> dict[str, str]:
    """Effective mode per dimension for one line."""
    out: dict[str, str] = {}
    for dim in DIMENSIONS:
        best: tuple[int, int, str] | None = None
        candidates = [r for r in rules if r.dimension == dim and matches(r, scope)]
        if dim in account_requires:
            candidates.append(Rule(dim, "account", scope.account_code, "REQUIRED", 0))
        for r in candidates:
            key = (r.priority, _STRICTNESS[r.mode], r.mode)
            if best is None or key > best:
                best = key
        out[dim] = best[2] if best else "ALLOWED"
    return out


def load_rules(ctx: TenantContext, company_id: uuid.UUID) -> list[Rule]:
    return [
        Rule(r.dimension, r.scope_type, r.scope_ref, r.mode, r.priority)
        for r in DimensionRule.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id).order_by("position")
    ]


def validate_rules(raw: Any) -> list[Rule]:
    if not isinstance(raw, list):
        raise ValidationFailed("rules must be a list", pointer="/rules", reason="type")
    if len(raw) > 1000:
        raise ValidationFailed("at most 1000 rules", pointer="/rules", reason="bulk_limit")
    out: list[Rule] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValidationFailed("rule must be an object", pointer=f"/rules/{i}", reason="type")
        extra = set(item) - {"dimension", "scope_type", "scope_ref", "mode", "priority"}
        if extra:
            raise ValidationFailed(
                "unknown rule field", pointer=f"/rules/{i}/{sorted(extra)[0]}", reason="additional_property"
            )
        dim, scope, ref, mode = item.get("dimension"), item.get("scope_type"), item.get("scope_ref"), item.get("mode")
        if dim not in DIMENSIONS:
            raise ValidationFailed("unknown dimension", pointer=f"/rules/{i}/dimension", reason="enum")
        if scope not in SCOPES:
            raise ValidationFailed("unknown scope_type", pointer=f"/rules/{i}/scope_type", reason="enum")
        if mode not in MODES:
            raise ValidationFailed("unknown mode", pointer=f"/rules/{i}/mode", reason="enum")
        if not isinstance(ref, str) or not 1 <= len(ref) <= 255:
            raise ValidationFailed("scope_ref must be 1-255 chars", pointer=f"/rules/{i}/scope_ref", reason="length")
        priority = item.get("priority", 0)
        if not isinstance(priority, int) or isinstance(priority, bool):
            raise ValidationFailed("priority must be an integer", pointer=f"/rules/{i}/priority", reason="type")
        out.append(Rule(dim, scope, ref, mode, priority))
    return out


def rule_set(ctx: TenantContext, company_id: uuid.UUID, *, lock: bool = False) -> DimensionRuleSet:
    qs = DimensionRuleSet.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id)
    if lock:
        qs = qs.select_for_update()
    row = qs.first()
    if row is None:
        row, _ = DimensionRuleSet.objects.get_or_create(
            tenant_id=ctx.tenant_id,
            company_id=company_id,
            defaults={"created_by": ctx.principal_id, "updated_by": ctx.principal_id},
        )
    return row


def replace_rules(uow: UnitOfWork, company_id: uuid.UUID, rules: list[Rule], if_match: str | None) -> DimensionRuleSet:
    ctx = uow.ctx
    ctx.require_company(company_id)
    rs = rule_set(ctx, company_id, lock=True)
    rs.check_if_match(if_match)
    DimensionRule.objects.filter(rule_set=rs).delete()
    DimensionRule.objects.bulk_create(
        [
            DimensionRule(
                tenant_id=ctx.tenant_id,
                company_id=company_id,
                rule_set=rs,
                dimension=r.dimension,
                scope_type=r.scope_type,
                scope_ref=r.scope_ref,
                mode=r.mode,
                priority=r.priority,
                position=i,
            )
            for i, r in enumerate(rules)
        ]
    )
    rs.save_versioned(ctx.principal_id, [])
    return rs
