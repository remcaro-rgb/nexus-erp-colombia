"""Implemented Django routes vs the frozen contract (UR-22 / C1-7 conformance runner).

``implemented_routes()`` walks the URL resolver of the running Django project and yields every
concrete route under ``/api/`` as an OpenAPI-style template (``<uuid:id>`` -> ``{id}``).
``match_contract()`` pairs each with a contract path; anything implemented but undocumented is a
contract drift (the UI may only use the public API, so nothing may exist outside it).

Django is imported lazily: the module is importable without settings.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from .openapi import Operation, operations, path_regex

_CONVERTER_RE = re.compile(r"<(?:(?P<conv>[a-z_]+):)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)>")
_REGEX_GROUP_RE = re.compile(r"\(\?P<(?P<name>[A-Za-z_][A-Za-z0-9_]*)>[^)]*\)")


@dataclass(frozen=True, slots=True)
class Route:
    template: str  # "/api/v1/sales/parties/{id}"
    name: str | None
    view: str


def _pattern_text(pattern: Any) -> str:
    raw = str(pattern.pattern) if hasattr(pattern, "pattern") else str(pattern)
    raw = raw.lstrip("^").rstrip("$").replace("\\Z", "")
    raw = _CONVERTER_RE.sub(lambda m: "{" + m.group("name") + "}", raw)
    raw = _REGEX_GROUP_RE.sub(lambda m: "{" + m.group("name") + "}", raw)
    return raw.replace("\\", "")


def _walk(patterns: Iterable[Any], prefix: str) -> Iterator[Route]:
    from django.urls import URLPattern, URLResolver

    for p in patterns:
        if isinstance(p, URLResolver):
            yield from _walk(p.url_patterns, prefix + _pattern_text(p.pattern))
        elif isinstance(p, URLPattern):
            cb = p.callback
            view = getattr(cb, "view_class", None) or getattr(cb, "cls", None) or cb
            yield Route(
                template="/" + (prefix + _pattern_text(p.pattern)).lstrip("/"),
                name=p.name,
                view=f"{getattr(view, '__module__', '?')}.{getattr(view, '__qualname__', repr(view))}",
            )


def implemented_routes(api_only: bool = True) -> list[Route]:
    from django.urls import get_resolver

    routes = sorted(set(_walk(get_resolver().url_patterns, "")), key=lambda r: r.template)
    if api_only:
        routes = [r for r in routes if r.template.startswith("/api/")]
    return routes


def _normalize(template: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", template.rstrip("/"))


def match_contract(routes: Iterable[Route], ops: Iterable[Operation] | None = None) -> dict[str, list[Operation]]:
    """Map each implemented route template to the contract operations with the same path."""
    by_norm: dict[str, list[Operation]] = {}
    for op in operations() if ops is None else ops:
        by_norm.setdefault(_normalize(op.path), []).append(op)
    return {r.template: by_norm.get(_normalize(r.template), []) for r in routes}


def resolves(path: str) -> bool:
    """True when a concrete path resolves to a Django view (route implemented)."""
    from django.urls import Resolver404, resolve

    try:
        resolve(path)
    except Resolver404:
        return False
    return True


def contract_path_for(concrete: str, ops: Iterable[Operation] | None = None) -> Operation | None:
    for op in operations() if ops is None else ops:
        if path_regex(op.path).match(concrete):
            return op
    return None
