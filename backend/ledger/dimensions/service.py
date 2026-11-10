"""Dimension masters: hierarchy rules DIM-R01…R07 (FEATURES-ADDENDUM F-ACC-DIM).

Every mutating function takes the caller's :class:`~kernel.uow.UnitOfWork`, so the
change, its closure rows, the audit record and the outbox event commit together.
Permission checks happen in the API layer (``ledger.dimensions.views``); company
access is always checked here through the tenant context.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Count, Q, QuerySet
from django.db.models.functions import Lower

from kernel import clock, services
from kernel.dimensions import DEFAULT_MAX_DEPTH, MAX_DEPTH, MIN_DEPTH
from kernel.errors import NotFound, ValidationFailed
from kernel.interfaces.audit import AuditEntry, AuditService
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork
from ledger.errors import DimensionInUse, ledger_error
from ledger.models import (
    DIMENSIONS,
    SLOT_BY_DIMENSION,
    DimensionClosure,
    DimensionNode,
    DimensionSettings,
    JournalLine,
)

AGRUPADOR = DimensionNode.NodeType.AGRUPADOR
DETALLE = DimensionNode.NodeType.DETALLE
ACTIVE = DimensionNode.Status.ACTIVE
ARCHIVED = DimensionNode.Status.ARCHIVED

EDITABLE_FIELDS = (
    "code",
    "description",
    "node_type",
    "parent_id",
    "valid_from",
    "valid_to",
    "sort_key",
    "external_ref",
)
EVENT = "ledger.dimension.changed.v1"


def check_dimension(dimension: str) -> str:
    if dimension not in DIMENSIONS:
        raise NotFound("unknown dimension")
    return dimension


def today() -> dt.date:
    return clock.business_date()


# --------------------------------------------------------------------------- settings


def max_depth(ctx: TenantContext, company_id: uuid.UUID, dimension: str) -> int:
    row = (
        DimensionSettings.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, dimension=dimension)
        .values_list("max_depth", flat=True)
        .first()
    )
    return int(row) if row is not None else DEFAULT_MAX_DEPTH


def set_max_depth(uow: UnitOfWork, company_id: uuid.UUID, dimension: str, value: int) -> DimensionSettings:
    ctx = uow.ctx
    ctx.require_company(company_id)
    check_dimension(dimension)
    if not MIN_DEPTH <= value <= MAX_DEPTH:
        raise ValidationFailed("max_depth must be between 1 and 10", pointer="/max_depth", reason="out_of_range")
    deepest = (
        DimensionNode.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, dimension=dimension)
        .order_by("-depth")
        .values_list("depth", flat=True)
        .first()
    )
    if deepest is not None and deepest > value:
        raise ledger_error("dimension_depth_exceeded", "existing nodes are deeper than the new limit", depth=deepest)
    row, created = DimensionSettings.objects.select_for_update().get_or_create(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        dimension=dimension,
        defaults={"max_depth": value, "created_by": ctx.principal_id, "updated_by": ctx.principal_id},
    )
    if not created and row.max_depth != value:
        row.max_depth = value
        row.save_versioned(ctx.principal_id, ["max_depth"])
    return row


# --------------------------------------------------------------------------- reads


def nodes_qs(ctx: TenantContext, company_id: uuid.UUID, dimension: str) -> QuerySet[DimensionNode]:
    ctx.require_company(company_id)
    return DimensionNode.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, dimension=dimension)


def get_node(
    ctx: TenantContext, company_id: uuid.UUID, dimension: str, node_id: Any, *, lock: bool = False
) -> DimensionNode:
    try:
        nid = uuid.UUID(str(node_id))
    except ValueError:
        raise NotFound("dimension node not found") from None
    qs = nodes_qs(ctx, company_id, dimension)
    if lock:
        qs = qs.select_for_update()
    node = qs.filter(id=nid).first()
    if node is None:
        raise NotFound("dimension node not found")
    return node


def usage_count(node: DimensionNode) -> int:
    slot = SLOT_BY_DIMENSION[node.dimension]
    return JournalLine.objects.filter(company_id=node.company_id, **{slot: node.id}).count()


def usage(node: DimensionNode) -> dict[str, Any]:
    lines = usage_count(node)
    children = DimensionNode.objects.filter(
        company_id=node.company_id, dimension=node.dimension, parent_id=node.id
    ).count()
    return {"references": lines, "by_type": {"journal_lines": lines, "children": children}}


def is_effective(node: DimensionNode, on: dt.date) -> bool:
    if node.status != ACTIVE:
        return False
    if node.valid_from and on < node.valid_from:
        return False
    return not (node.valid_to and on > node.valid_to)


def subtree_ids(company_id: uuid.UUID, root_id: uuid.UUID, valid_on: dt.date | None = None) -> list[uuid.UUID]:
    """Ids of ``root_id`` and all its descendants (closure table, as of ``valid_on``)."""
    qs = DimensionClosure.objects.filter(company_id=company_id, ancestor_id=root_id)
    if valid_on is None:
        qs = qs.filter(valid_to__isnull=True)
    else:
        qs = qs.filter(valid_from__lte=valid_on).filter(Q(valid_to__isnull=True) | Q(valid_to__gte=valid_on))
    return list(qs.values_list("descendant_id", flat=True))


def _children_map(nodes: list[DimensionNode]) -> dict[uuid.UUID | None, list[DimensionNode]]:
    out: dict[uuid.UUID | None, list[DimensionNode]] = defaultdict(list)
    for n in nodes:
        out[n.parent_id].append(n)
    for kids in out.values():
        kids.sort(key=lambda n: ((n.sort_key or ""), n.code.lower()))
    return out


def tree(
    ctx: TenantContext,
    company_id: uuid.UUID,
    dimension: str,
    *,
    root: uuid.UUID | None = None,
    depth: int | None = None,
    valid_on: dt.date | None = None,
    allowed_ids: set[uuid.UUID] | None = None,
) -> list[dict[str, Any]]:
    """Nested ``TreeNode`` list (root.yaml TreeNode) of the current hierarchy."""
    nodes = list(nodes_qs(ctx, company_id, dimension))
    if valid_on is not None:
        nodes = [
            n
            for n in nodes
            if (not n.valid_from or n.valid_from <= valid_on) and (not n.valid_to or n.valid_to >= valid_on)
        ]
    by_id = {n.id: n for n in nodes}
    kids = _children_map(nodes)

    def build(n: DimensionNode, level: int) -> dict[str, Any]:
        children = [] if depth is not None and level >= depth else [build(c, level + 1) for c in kids.get(n.id, [])]
        return {
            "id": str(n.id),
            "code": n.code,
            "label": n.description,
            "node_type": n.node_type,
            "status": n.status,
            "path": n.path,
            "depth": n.depth,
            "children": children,
        }

    if root is not None:
        if root not in by_id:
            raise NotFound("dimension node not found")
        starts = [by_id[root]]
    else:
        starts = [n for n in kids.get(None, [])] + [
            n for pid, ns in kids.items() if pid is not None and pid not in by_id for n in ns
        ]
    result = [build(n, 1) for n in starts]
    if allowed_ids is not None:
        result = _prune(result, {str(i) for i in allowed_ids})
    return result


def _prune(items: list[dict[str, Any]], allowed: set[str]) -> list[dict[str, Any]]:
    out = []
    for it in items:
        if it["id"] in allowed:
            out.append(it)
        else:
            out.extend(_prune(it["children"], allowed))
    return out


# --------------------------------------------------------------------------- validation helpers


def _clean_code(value: Any) -> str:
    code = str(value or "").strip()
    if not 1 <= len(code) <= 40:
        raise ValidationFailed("code must have 1 to 40 characters", pointer="/code", reason="length")
    return code


def _clean_description(value: Any) -> str:
    text = str(value or "").strip()
    if not 1 <= len(text) <= 200:
        raise ValidationFailed("description must have 1 to 200 characters", pointer="/description", reason="length")
    return text


def _node_type(value: Any) -> str:
    if value not in (AGRUPADOR, DETALLE):
        raise ValidationFailed("node_type must be AGRUPADOR or DETALLE", pointer="/node_type", reason="enum")
    return str(value)


def _date(value: Any, pointer: str, *, required: bool) -> dt.date | None:
    if value in (None, ""):
        if required:
            raise ValidationFailed("date required", pointer=pointer, reason="required")
        return None
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        raise ValidationFailed("invalid date", pointer=pointer, reason="format") from None


def _short(value: Any, pointer: str) -> str | None:
    if value is None:
        return None
    text = str(value)
    if len(text) > 255:
        raise ValidationFailed("too long", pointer=pointer, reason="length")
    return text


def _assert_code_free(
    ctx: TenantContext, company_id: uuid.UUID, dimension: str, code: str, exclude: uuid.UUID | None
) -> None:
    qs = DimensionNode.objects.annotate(lc=Lower("code")).filter(
        tenant_id=ctx.tenant_id, company_id=company_id, dimension=dimension, lc=code.lower()
    )
    if exclude is not None:
        qs = qs.exclude(id=exclude)
    if qs.exists():
        raise ledger_error("dimension_code_duplicate", f"code {code!r} already exists in this dimension")


def _resolve_parent(ctx: TenantContext, company_id: uuid.UUID, dimension: str, parent_id: Any) -> DimensionNode | None:
    if parent_id in (None, ""):
        return None
    try:
        pid = uuid.UUID(str(parent_id))
    except ValueError:
        raise ValidationFailed("parent_id must be a UUID", pointer="/parent_id", reason="format") from None
    parent = (
        DimensionNode.objects.select_for_update()
        .filter(tenant_id=ctx.tenant_id, company_id=company_id, dimension=dimension, id=pid)
        .first()
    )
    if parent is not None:
        if parent.node_type != AGRUPADOR:
            raise ValidationFailed(
                "the parent must be an AGRUPADOR (a DETALLE cannot have children)",
                pointer="/parent_id",
                reason="parent_not_agrupador",
            )
        return parent
    other = DimensionNode.objects.filter(tenant_id=ctx.tenant_id, id=pid).values_list("company_id", "dimension").first()
    if other is not None and other[0] != company_id and other[0] in ctx.company_ids:
        raise ledger_error("dimension_cross_company", "the parent belongs to another company")
    if other is not None and other[0] == company_id:
        raise ValidationFailed(
            "the parent belongs to another dimension", pointer="/parent_id", reason="parent_other_dimension"
        )
    # Unknown or not visible: same answer, no existence oracle.
    raise ledger_error("dimension_cross_company", "the parent is not a node of this company and dimension")


def _subtree_height(node: DimensionNode) -> int:
    """Levels below ``node`` (0 for a leaf), from the current closure."""
    deepest = (
        DimensionClosure.objects.filter(company_id=node.company_id, ancestor_id=node.id, valid_to__isnull=True)
        .order_by("-distance")
        .values_list("distance", flat=True)
        .first()
    )
    return int(deepest or 0)


def _audit(uow: UnitOfWork, action: str, node: DimensionNode, changes: dict[str, Any], reason: str = "") -> None:
    audit = services.optional(AuditService)
    if audit is not None:
        audit.record(
            uow,
            AuditEntry(
                action=f"ledger.dimension.{action}",
                resource_type=f"ledger.dimension.{node.dimension}",
                resource_id=node.id,
                company_id=node.company_id,
                changes=changes,
                reason=reason,
            ),
        )


def _emit(uow: UnitOfWork, node: DimensionNode, change: str) -> None:
    payload: dict[str, Any] = {"node_id": str(node.id), "dimension": node.dimension, "change": change}
    if node.parent_id is not None:
        payload["parent_id"] = str(node.parent_id)
    uow.emit(EVENT, payload, company_id=node.company_id)


def _closure_insert(node: DimensionNode, parent: DimensionNode | None, on: dt.date) -> None:
    rows = [
        DimensionClosure(
            tenant_id=node.tenant_id,
            company_id=node.company_id,
            dimension=node.dimension,
            ancestor_id=node.id,
            descendant_id=node.id,
            distance=0,
            valid_from=on,
        )
    ]
    if parent is not None:
        for anc_id, dist in DimensionClosure.objects.filter(
            company_id=node.company_id, descendant_id=parent.id, valid_to__isnull=True
        ).values_list("ancestor_id", "distance"):
            rows.append(
                DimensionClosure(
                    tenant_id=node.tenant_id,
                    company_id=node.company_id,
                    dimension=node.dimension,
                    ancestor_id=anc_id,
                    descendant_id=node.id,
                    distance=dist + 1,
                    valid_from=on,
                )
            )
    DimensionClosure.objects.bulk_create(rows)


def _translate_integrity(exc: IntegrityError) -> Exception:
    text = str(exc)
    if "ledger_dim_code_ci_uq" in text:
        return ledger_error("dimension_code_duplicate", "code already exists in this dimension")
    if "ledger_dim_parent_fk" in text:
        return ledger_error("dimension_cross_company", "the parent is not a node of this company and dimension")
    return exc


# --------------------------------------------------------------------------- commands


@dataclass(frozen=True, slots=True)
class NodeInput:
    code: str
    description: str
    node_type: str
    parent_id: Any = None
    valid_from: Any = None
    valid_to: Any = None
    sort_key: Any = None
    external_ref: Any = None

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> NodeInput:
        unknown = set(data) - set(EDITABLE_FIELDS)
        if unknown:
            raise ValidationFailed("unknown fields", pointer="/" + sorted(unknown)[0], reason="additional_property")
        for required in ("code", "description", "node_type"):
            if required not in data:
                raise ValidationFailed(f"{required} is required", pointer=f"/{required}", reason="required")
        return cls(**{k: data.get(k) for k in EDITABLE_FIELDS})


def create_node(uow: UnitOfWork, company_id: uuid.UUID, dimension: str, data: NodeInput) -> DimensionNode:
    ctx = uow.ctx
    ctx.require_company(company_id)
    check_dimension(dimension)
    code = _clean_code(data.code)
    description = _clean_description(data.description)
    node_type = _node_type(data.node_type)
    valid_from = _date(data.valid_from, "/valid_from", required=False) or today()
    valid_to = _date(data.valid_to, "/valid_to", required=False)
    if valid_to is not None and valid_to < valid_from:
        raise ValidationFailed("valid_to before valid_from", pointer="/valid_to", reason="range")
    parent = _resolve_parent(ctx, company_id, dimension, data.parent_id)
    if parent is not None and parent.status != ACTIVE:
        raise ledger_error("dimension_archived", "the parent is archived")
    depth = (parent.depth + 1) if parent else 1
    limit = max_depth(ctx, company_id, dimension)
    if depth > limit:
        raise ledger_error("dimension_depth_exceeded", f"depth {depth} exceeds the limit {limit}", max_depth=limit)
    _assert_code_free(ctx, company_id, dimension, code, None)
    node = DimensionNode(
        tenant_id=ctx.tenant_id,
        company_id=company_id,
        dimension=dimension,
        code=code,
        description=description,
        node_type=node_type,
        parent_id=parent.id if parent else None,
        depth=depth,
        path=f"{parent.path}/{code}" if parent else code,
        valid_from=valid_from,
        valid_to=valid_to,
        sort_key=_short(data.sort_key, "/sort_key"),
        external_ref=_short(data.external_ref, "/external_ref"),
        created_by=ctx.principal_id,
        updated_by=ctx.principal_id,
    )
    try:
        with transaction.atomic(using=uow.using):
            node.save(force_insert=True)
    except IntegrityError as exc:
        raise _translate_integrity(exc) from exc
    _closure_insert(node, parent, today())
    _audit(uow, "create", node, {"code": code, "node_type": node_type, "parent_id": str(node.parent_id or "")})
    _emit(uow, node, "created")
    return node


def _refresh_paths(root: DimensionNode) -> None:
    """Recompute ``path``/``depth`` of every descendant of ``root`` (after move/rename)."""
    nodes = list(
        DimensionNode.objects.filter(company_id=root.company_id, dimension=root.dimension).only(
            "id", "parent_id", "code", "path", "depth"
        )
    )
    kids = _children_map(nodes)
    stack = [(root.id, root.path, root.depth)]
    changed: list[DimensionNode] = []
    while stack:
        pid, ppath, pdepth = stack.pop()
        for child in kids.get(pid, []):
            child.path = f"{ppath}/{child.code}"
            child.depth = pdepth + 1
            changed.append(child)
            stack.append((child.id, child.path, child.depth))
    if changed:
        DimensionNode.objects.bulk_update(changed, ["path", "depth"])


def move_node(uow: UnitOfWork, node: DimensionNode, new_parent_id: Any) -> DimensionNode:
    """Re-parent ``node`` (DIM-R02/R03/R06): cycle, parent type, company and depth checks;
    closure rebuilt effective today; posted lines untouched."""
    ctx = uow.ctx
    new_parent = _resolve_parent(ctx, node.company_id, node.dimension, new_parent_id)
    if new_parent is not None:
        if new_parent.id == node.id or new_parent.id in set(subtree_ids(node.company_id, node.id)):
            raise ledger_error("dimension_cycle", "a node cannot move under itself or its descendants")
        if new_parent.status != ACTIVE:
            raise ledger_error("dimension_archived", "the new parent is archived")
    new_depth = (new_parent.depth + 1) if new_parent else 1
    limit = max_depth(ctx, node.company_id, node.dimension)
    if new_depth + _subtree_height(node) > limit:
        raise ledger_error(
            "dimension_depth_exceeded",
            f"the moved subtree would reach depth {new_depth + _subtree_height(node)} (limit {limit})",
            max_depth=limit,
        )
    old_parent = node.parent_id
    if old_parent == (new_parent.id if new_parent else None):
        return node
    on = today()
    subtree = list(
        DimensionClosure.objects.filter(
            company_id=node.company_id, ancestor_id=node.id, valid_to__isnull=True
        ).values_list("descendant_id", "distance")
    )
    members = [d for d, _ in subtree]
    stale = DimensionClosure.objects.filter(
        company_id=node.company_id, descendant_id__in=members, valid_to__isnull=True
    ).exclude(ancestor_id__in=members)
    stale.filter(valid_from__gte=on).delete()
    stale.update(valid_to=on - dt.timedelta(days=1))
    if new_parent is not None:
        ancestors = list(
            DimensionClosure.objects.filter(
                company_id=node.company_id, descendant_id=new_parent.id, valid_to__isnull=True
            ).values_list("ancestor_id", "distance")
        )
        DimensionClosure.objects.bulk_create(
            [
                DimensionClosure(
                    tenant_id=node.tenant_id,
                    company_id=node.company_id,
                    dimension=node.dimension,
                    ancestor_id=anc,
                    descendant_id=desc,
                    distance=adist + 1 + ddist,
                    valid_from=on,
                )
                for anc, adist in ancestors
                for desc, ddist in subtree
            ]
        )
    node.parent_id = new_parent.id if new_parent else None
    node.depth = new_depth
    node.path = f"{new_parent.path}/{node.code}" if new_parent else node.code
    node.save_versioned(ctx.principal_id, ["parent_id", "depth", "path"])
    _refresh_paths(node)
    _audit(
        uow,
        "move",
        node,
        {"parent_id": {"from": str(old_parent or ""), "to": str(node.parent_id or "")}},
    )
    _emit(uow, node, "moved")
    return node


def update_node(uow: UnitOfWork, node: DimensionNode, data: dict[str, Any], if_match: str | None) -> DimensionNode:
    ctx = uow.ctx
    node.check_if_match(if_match)
    unknown = set(data) - set(EDITABLE_FIELDS)
    if unknown:
        raise ValidationFailed("unknown fields", pointer="/" + sorted(unknown)[0], reason="additional_property")
    if not data:
        raise ValidationFailed("empty patch", pointer="/", reason="min_properties")
    changes: dict[str, Any] = {}
    fields: list[str] = []
    if "parent_id" in data:
        new_parent = data["parent_id"] or None
        if str(new_parent or "") != str(node.parent_id or ""):
            node = move_node(uow, node, new_parent)
    if "node_type" in data:
        new_type = _node_type(data["node_type"])
        if new_type != node.node_type:
            if usage_count(node):
                raise DimensionInUse("a used node cannot change node_type")
            if (
                new_type == DETALLE
                and DimensionNode.objects.filter(
                    company_id=node.company_id, dimension=node.dimension, parent_id=node.id
                ).exists()
            ):
                raise ValidationFailed(
                    "an AGRUPADOR with children cannot become DETALLE", pointer="/node_type", reason="has_children"
                )
            changes["node_type"] = {"from": node.node_type, "to": new_type}
            node.node_type = new_type
            fields.append("node_type")
    if "code" in data:
        code = _clean_code(data["code"])
        if code != node.code:
            _assert_code_free(ctx, node.company_id, node.dimension, code, node.id)
            changes["code"] = {"from": node.code, "to": code}
            node.code = code
            node.path = (node.path.rsplit("/", 1)[0] + "/" + code) if "/" in node.path else code
            fields += ["code", "path"]
    if "description" in data:
        text = _clean_description(data["description"])
        if text != node.description:
            changes["description"] = {"from": node.description, "to": text}
            node.description = text
            fields.append("description")
    for name in ("valid_from", "valid_to"):
        if name in data:
            value = _date(data[name], f"/{name}", required=(name == "valid_from"))
            if value != getattr(node, name):
                changes[name] = {"from": str(getattr(node, name) or ""), "to": str(value or "")}
                setattr(node, name, value)
                fields.append(name)
    if node.valid_to is not None and node.valid_from and node.valid_to < node.valid_from:
        raise ValidationFailed("valid_to before valid_from", pointer="/valid_to", reason="range")
    for name in ("sort_key", "external_ref"):
        if name in data:
            value = _short(data[name], f"/{name}")
            if value != getattr(node, name):
                setattr(node, name, value)
                fields.append(name)
    if fields:
        try:
            with transaction.atomic(using=uow.using):
                node.save_versioned(ctx.principal_id, fields)
        except IntegrityError as exc:
            raise _translate_integrity(exc) from exc
        if "code" in fields:
            _refresh_paths(node)
        _audit(uow, "update", node, changes)
        _emit(uow, node, "updated")
    return node


def archive_node(uow: UnitOfWork, node: DimensionNode, reason: str = "") -> DimensionNode:
    """Archive: blocks new references from today; posted lines keep the id (DIM-R05)."""
    if node.status == ARCHIVED:
        return node
    node.status = ARCHIVED
    node.archived_at = today()
    node.save_versioned(uow.ctx.principal_id, ["status", "archived_at"])
    _audit(uow, "archive", node, {"status": {"from": ACTIVE, "to": ARCHIVED}}, reason)
    _emit(uow, node, "archived")
    return node


def unarchive_node(uow: UnitOfWork, node: DimensionNode, reason: str = "") -> DimensionNode:
    if node.status == ACTIVE:
        return node
    if node.parent_id is not None:
        parent = DimensionNode.objects.filter(company_id=node.company_id, id=node.parent_id).first()
        if parent is not None and parent.status != ACTIVE:
            raise ledger_error("dimension_archived", "the parent is archived; unarchive it first")
    node.status = ACTIVE
    node.archived_at = None
    node.save_versioned(uow.ctx.principal_id, ["status", "archived_at"])
    _audit(uow, "unarchive", node, {"status": {"from": ARCHIVED, "to": ACTIVE}}, reason)
    _emit(uow, node, "unarchived")
    return node


def delete_node(uow: UnitOfWork, node: DimensionNode, if_match: str | None) -> None:
    """Delete only unused leaves (no posted lines, no children); otherwise ``dimension_in_use``."""
    node.check_if_match(if_match)
    info = usage(node)
    if info["references"] or info["by_type"]["children"]:
        raise DimensionInUse("the node is used; archive it instead", **info)
    _audit(uow, "delete", node, {"code": node.code})
    DimensionClosure.objects.filter(company_id=node.company_id, descendant_id=node.id).delete()
    DimensionClosure.objects.filter(company_id=node.company_id, ancestor_id=node.id).delete()
    node.delete()


# --------------------------------------------------------------------------- posting checks


def check_line_reference(
    ctx: TenantContext,
    company_id: uuid.UUID,
    dimension: str,
    node_id: uuid.UUID,
    on: dt.date,
    *,
    allow_archived: bool = False,
    cache: dict[uuid.UUID, DimensionNode | None] | None = None,
) -> DimensionNode:
    """DIM-R01/R04/R05 for one line slot: same company, DETALLE, active and effective."""
    node: DimensionNode | None
    if cache is not None and node_id in cache:
        node = cache[node_id]
    else:
        node = DimensionNode.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, id=node_id).first()
        if cache is not None:
            cache[node_id] = node
    if node is None:
        raise ledger_error(
            "dimension_cross_company", f"{dimension} node is not a node of this company", dimension=dimension
        )
    if node.dimension != dimension:
        raise ValidationFailed(
            f"node belongs to dimension {node.dimension}", pointer=f"/{dimension}", reason="dimension_mismatch"
        )
    if node.node_type != DETALLE:
        raise ledger_error(
            "dimension_node_not_postable",
            f"{dimension} {node.code} is an AGRUPADOR",
            dimension=dimension,
            node_code=node.code,
        )
    if not allow_archived and not is_effective(node, on):
        raise ledger_error(
            "dimension_archived", f"{dimension} {node.code} is archived or not valid on {on}", dimension=dimension
        )
    return node


def child_counts(company_id: uuid.UUID, dimension: str) -> dict[uuid.UUID, int]:
    rows = (
        DimensionNode.objects.filter(company_id=company_id, dimension=dimension, parent_id__isnull=False)
        .values("parent_id")
        .annotate(n=Count("id"))
    )
    return {r["parent_id"]: r["n"] for r in rows}
