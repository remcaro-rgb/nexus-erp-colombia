"""First-party app discovery and URL mounting (FIX-CI-W02; later waves need no settings/URL edit).

Rule (deterministic):

* **Apps.** Every package ``backend/<app>/`` that has both ``__init__.py`` and ``apps.py`` is a first-party
  Django app and is installed: ``kernel`` first, then ``iam``, then every other app sorted by name.
* **Routes.** Every installed first-party app (except ``kernel``, whose routes are listed explicitly in
  ``erp.urls``) that has a ``<app>/urls.py`` is mounted at ``api/v1/<label>/``, unless its ``AppConfig``
  declares ``URL_MOUNT`` (e.g. ``iam`` uses ``""`` because its routes are already absolute).
  ``EXTRA_URL_MOUNTS`` (``{prefix: ("module:attribute", namespace)}``) mounts further pattern lists
  (e.g. workflow's ``api/v1/kpis/``).
"""

from __future__ import annotations

import importlib
import importlib.util
from collections.abc import Iterable
from pathlib import Path
from typing import Any

#: Apps installed before the others (in this order) when present.
FIRST_APPS = ("kernel", "iam")
#: First-party apps whose routes are not auto-mounted (``erp.urls`` lists them explicitly).
EXPLICIT_ROUTES = frozenset({"kernel"})


def discover_apps(base_dir: Path) -> list[str]:
    """First-party app packages under ``base_dir`` (``<app>/__init__.py`` + ``<app>/apps.py``), ordered."""
    found = sorted(
        p.parent.name
        for p in base_dir.glob("*/apps.py")
        if (p.parent / "__init__.py").is_file() and p.parent.name.isidentifier()
    )
    first = [a for a in FIRST_APPS if a in found]
    return first + [a for a in found if a not in first]


def default_mount(label: str) -> str:
    return f"api/v1/{label}/"


def _resolve(ref: str) -> Any:
    module, _, attr = ref.partition(":")
    obj: Any = importlib.import_module(module)
    return getattr(obj, attr) if attr else obj


def app_url_patterns(app_configs: Iterable[Any], first_party: Iterable[str]) -> list[Any]:
    """``path(...)`` entries mounting the routes of the installed first-party apps (see module doc)."""
    from django.urls import include, path

    local = set(first_party)
    patterns: list[Any] = []
    for cfg in app_configs:
        if cfg.name not in local or cfg.name in EXPLICIT_ROUTES:
            continue
        urls_module = f"{cfg.name}.urls"
        if importlib.util.find_spec(urls_module) is None:
            continue
        mount = getattr(cfg, "URL_MOUNT", None)
        patterns.append(path(default_mount(cfg.label) if mount is None else str(mount), include(urls_module)))
        extra: dict[str, tuple[str, str]] = getattr(cfg, "EXTRA_URL_MOUNTS", {}) or {}
        for prefix, (ref, namespace) in sorted(extra.items()):
            patterns.append(path(prefix, include((_resolve(ref), namespace))))
    return patterns
