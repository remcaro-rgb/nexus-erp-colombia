"""Index over the frozen OpenAPI bundle (``contracts/openapi/openapi.json``).

Everything the P22 suites generate (leak cases, failure-injection targets, conformance runs)
starts from :func:`operations`. The index is pure data: no Django import.

Money / stock effects (T-P22-FIN-01 / T-P22-STK-01) come from the operation extensions
``x-money-effect: true`` / ``x-stock-effect: true``. P22 asked P02 to add them
(``contracts/change-requests/P22a-1.md``). Until the bundle carries at least one of them, the
index falls back to a **derived** classification from shared/07 (posting triggers) and marks
every such operation with ``effect_source = "derived-shared07"`` so the evidence never presents
the fallback as the contract.
"""

from __future__ import annotations

import enum
import functools
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from .paths import openapi_bundle

METHODS = ("get", "post", "put", "patch", "delete")

CLASSIFICATION_ORDER = ("public", "internal", "confidential", "restricted-pii", "sensitive-pii", "secret")
CLASS_RANK = {name: i for i, name in enumerate(CLASSIFICATION_ORDER)}


class Channel(enum.StrEnum):
    """Read channels of invariant 12 (plus WRITE for state-changing operations)."""

    LIST = "list"
    DETAIL = "detail"
    SEARCH = "search"
    EXPORT = "export"
    REPORT = "report"
    AI = "ai"
    MCP = "mcp"
    WEBHOOK = "webhook"
    PORTAL = "portal"
    WRITE = "write"


# shared/07 posting triggers -> operations (fallback until P02 tags x-money-effect / x-stock-effect).
_MONEY_VERBS = frozenset({"post", "reverse", "dispose"})
_MONEY_EXTRA = frozenset(
    {
        ("post", "/api/v1/pos/tickets:checkout"),
        ("post", "/api/v1/pos/sessions/{id}:close"),
        ("post", "/api/v1/sales/deliveries/{id}:validate"),
        ("post", "/api/v1/purchasing/receipts/{id}:validate"),
        ("post", "/api/v1/onboarding/opening-balances/{id}:commit"),
        ("post", "/api/v1/assets/assets/{id}:transfer"),
    }
)
_STOCK_PREFIXES = ("/api/v1/inventory/",)
_STOCK_EXTRA = frozenset(
    {
        ("post", "/api/v1/pos/tickets:checkout"),
        ("post", "/api/v1/pos/returns/{id}:post"),
        ("post", "/api/v1/sales/deliveries/{id}:validate"),
        ("post", "/api/v1/sales/returns/{id}:post"),
        ("post", "/api/v1/sales/credit-notes/{id}:post"),
        ("post", "/api/v1/purchasing/receipts/{id}:validate"),
        ("post", "/api/v1/purchasing/vendor-credit-notes/{id}:post"),
        ("post", "/api/v1/purchasing/landed-costs/{id}:post"),
        ("post", "/api/v1/inventory/transfers/{id}:ship"),
        ("post", "/api/v1/inventory/transfers/{id}:receive"),
    }
)
_STOCK_VERBS = frozenset({"post", "reverse"})

_PARAM_RE = re.compile(r"\{([^}/]+)\}")


@dataclass(frozen=True, slots=True)
class Operation:
    method: str
    path: str
    operation_id: str
    permission: str
    owner: str
    action: str | None
    path_params: tuple[str, ...]
    channel: Channel
    money_effect: bool
    stock_effect: bool
    effect_source: str
    idempotent: bool
    features: tuple[str, ...] = ()
    error_codes: tuple[str, ...] = ()
    response_fields: tuple[tuple[str, str], ...] = field(default=(), compare=False)
    request_fields: tuple[tuple[str, str], ...] = field(default=(), compare=False)
    response_is_page: bool = False
    portal: bool = False

    @property
    def key(self) -> str:
        return f"{self.method.upper()} {self.path}"

    @property
    def resource(self) -> str:
        """``<module>.<resource>`` of the permission code (row-ownership key)."""
        parts = self.permission.split(".")
        return ".".join(parts[:2]) if len(parts) >= 2 else self.permission

    @property
    def permission_action(self) -> str:
        return self.permission.rsplit(".", 1)[-1]

    @property
    def module(self) -> str:
        return self.permission.split(".", 1)[0]

    @property
    def is_read(self) -> bool:
        return self.channel is not Channel.WRITE

    @property
    def targets_one_object(self) -> bool:
        return "id" in self.path_params

    def field_classes(self) -> dict[str, str]:
        return dict(self.response_fields)

    def fields_above(self, max_classification: str) -> frozenset[str]:
        """Response fields a principal with ``max_classification`` must never receive."""
        limit = CLASS_RANK[max_classification]
        return frozenset(name for name, cls in self.response_fields if CLASS_RANK.get(cls, len(CLASS_RANK)) > limit)


def resolve(doc: dict[str, Any], node: Any) -> Any:
    hops = 0
    while isinstance(node, dict) and isinstance(node.get("$ref"), str) and node["$ref"].startswith("#/"):
        cur: Any = doc
        for part in node["$ref"][2:].split("/"):
            cur = cur[part]
        node = cur
        hops += 1
        if hops > 50:
            raise ValueError("$ref loop")
    return node


def _schema_fields(doc: dict[str, Any], schema: Any, prefix: str = "", depth: int = 0) -> Iterator[tuple[str, str]]:
    """Yield ``(dotted_name, classification)`` for every property (nested objects/arrays)."""
    schema = resolve(doc, schema)
    if not isinstance(schema, dict) or depth > 6:
        return
    for combo in ("allOf", "oneOf", "anyOf"):
        for sub in schema.get(combo, []):
            yield from _schema_fields(doc, sub, prefix, depth + 1)
    for name, prop in (schema.get("properties") or {}).items():
        cls = prop.get("x-classification") if isinstance(prop, dict) else None
        target = resolve(doc, prop)
        cls = cls or (target.get("x-classification") if isinstance(target, dict) else None) or "internal"
        dotted = f"{prefix}{name}"
        yield dotted, cls
        if isinstance(target, dict):
            if target.get("type") == "array" or "items" in target:
                yield from _schema_fields(doc, target.get("items", {}), dotted + "[].", depth + 1)
            elif target.get("properties"):
                yield from _schema_fields(doc, target, dotted + ".", depth + 1)


def _ok_schema(doc: dict[str, Any], op: dict[str, Any]) -> Any:
    for status in ("200", "201", "202"):
        resp = op.get("responses", {}).get(status)
        if resp is None:
            continue
        resp = resolve(doc, resp)
        content = resp.get("content", {})
        for media in ("application/json", "application/problem+json"):
            if media in content and "schema" in content[media]:
                return content[media]["schema"]
    return None


def _page_item_schema(doc: dict[str, Any], schema: Any) -> Any | None:
    target = resolve(doc, schema)
    if not isinstance(target, dict):
        return None
    props: dict[str, Any] = {}
    for part in [resolve(doc, p) for p in target.get("allOf", [])] + [target]:
        if isinstance(part, dict):
            props.update(part.get("properties") or {})
    if "items" in props and "next_cursor" in props:
        items = resolve(doc, props["items"])
        return items.get("items") if isinstance(items, dict) else None
    return None


def _request_schema(doc: dict[str, Any], op: dict[str, Any]) -> Any:
    body = op.get("requestBody")
    if not body:
        return None
    body = resolve(doc, body)
    content = body.get("content", {})
    for media, spec in content.items():
        if "json" in media and "schema" in spec:
            return spec["schema"]
    return None


def classify_channel(method: str, path: str, action: str | None, permission: str) -> Channel:
    if path.startswith("/api/portal/"):
        return Channel.PORTAL if method == "get" else Channel.WRITE
    if action == "export" or "/exports" in path or permission.endswith(".export"):
        return Channel.EXPORT
    if method != "get":
        return Channel.WRITE
    if path.startswith("/api/v1/ai/"):
        return Channel.AI
    if path.startswith("/api/v1/mcp") or "/mcp/" in path:
        return Channel.MCP
    if path.startswith("/api/v1/reporting/"):
        return Channel.REPORT
    last = path.rsplit("/", 1)[-1]
    if "search" in last or action == "search":
        return Channel.SEARCH
    if last.startswith("{") and last.endswith("}"):
        return Channel.DETAIL
    return Channel.LIST


def _action_of(path: str) -> str | None:
    last = path.rsplit("/", 1)[-1]
    return last.rsplit(":", 1)[1] if ":" in last else None


def _tagged(doc: dict[str, Any]) -> bool:
    for item in doc["paths"].values():
        for m in METHODS:
            op = item.get(m)
            if op and ("x-money-effect" in op or "x-stock-effect" in op):
                return True
    return False


def _derived_money(method: str, path: str, action: str | None) -> bool:
    if (method, path) in _MONEY_EXTRA:
        return True
    return method == "post" and action in _MONEY_VERBS and not path.startswith("/api/v1/consolidation/")


def _derived_stock(method: str, path: str, action: str | None) -> bool:
    if (method, path) in _STOCK_EXTRA:
        return True
    return method == "post" and action in _STOCK_VERBS and path.startswith(_STOCK_PREFIXES)


def build_index(doc: dict[str, Any]) -> tuple[Operation, ...]:
    tagged = _tagged(doc)
    out: list[Operation] = []
    for path, item in doc["paths"].items():
        for method in METHODS:
            op = item.get(method)
            if op is None:
                continue
            action = _action_of(path)
            permission = op.get("x-permission", "")
            ok = _ok_schema(doc, op)
            item_schema = _page_item_schema(doc, ok) if ok is not None else None
            fields_src = item_schema if item_schema is not None else ok
            resp_fields = tuple(sorted(set(_schema_fields(doc, fields_src)))) if fields_src is not None else ()
            req = _request_schema(doc, op)
            req_fields = tuple(sorted(set(_schema_fields(doc, req)))) if req is not None else ()
            if tagged:
                money = bool(op.get("x-money-effect", False))
                stock = bool(op.get("x-stock-effect", False))
                source = "contract"
            else:
                money = _derived_money(method, path, action)
                stock = _derived_stock(method, path, action)
                source = "derived-shared07" if (money or stock) else "none"
            out.append(
                Operation(
                    method=method,
                    path=path,
                    operation_id=op.get("operationId", ""),
                    permission=permission,
                    owner=op.get("x-owner", ""),
                    action=action,
                    path_params=tuple(_PARAM_RE.findall(path)),
                    channel=classify_channel(method, path, action, permission),
                    money_effect=money,
                    stock_effect=stock,
                    effect_source=source,
                    idempotent=bool(op.get("x-idempotent", False)),
                    features=tuple(op.get("x-features", ())),
                    error_codes=tuple(op.get("x-error-codes", ())),
                    response_fields=resp_fields,
                    request_fields=req_fields,
                    response_is_page=item_schema is not None,
                    portal=bool(op.get("x-portal", False)) or path.startswith("/api/portal/"),
                )
            )
    return tuple(out)


@dataclass(frozen=True, slots=True)
class Webhook:
    event_type: str
    fields: tuple[tuple[str, str], ...]


def build_webhooks(doc: dict[str, Any]) -> tuple[Webhook, ...]:
    out: list[Webhook] = []
    for name, item in (doc.get("webhooks") or {}).items():
        op = item.get("post") or {}
        event_type = op.get("x-event-type", name)
        schema = _request_schema(doc, op)
        flds = tuple(sorted(set(_schema_fields(doc, schema)))) if schema is not None else ()
        out.append(Webhook(event_type=event_type, fields=flds))
    return tuple(out)


@functools.cache
def operations() -> tuple[Operation, ...]:
    return build_index(openapi_bundle())


@functools.cache
def webhooks() -> tuple[Webhook, ...]:
    return build_webhooks(openapi_bundle())


def by_key() -> dict[str, Operation]:
    return {op.key: op for op in operations()}


def select(
    ops: Iterable[Operation] | None = None,
    *,
    channels: Iterable[Channel] | None = None,
    money: bool | None = None,
    stock: bool | None = None,
    module: str | None = None,
) -> list[Operation]:
    chosen = set(channels) if channels is not None else None
    res = []
    for op in operations() if ops is None else ops:
        if chosen is not None and op.channel not in chosen:
            continue
        if money is not None and op.money_effect != money:
            continue
        if stock is not None and op.stock_effect != stock:
            continue
        if module is not None and op.module != module:
            continue
        res.append(op)
    return res


def path_regex(template: str) -> re.Pattern[str]:
    """Regex matching concrete paths of an OpenAPI path template (``{x}`` = one segment)."""
    parts = _PARAM_RE.split(template)
    rx = ""
    for i, part in enumerate(parts):
        rx += "[^/:]+" if i % 2 else re.escape(part)
    return re.compile("^" + rx + "$")
