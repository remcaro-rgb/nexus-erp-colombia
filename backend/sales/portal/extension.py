"""Extension bootstrap of the P10b part (``sales.portal``) inside the ``sales`` app (owner P10a).

P10b owns only ``backend/sales/portal/**``; the app's ``models.py``, ``migrations/`` and ``apps.py`` belong to
P10a. The same split was solved for purchasing by P11b (``purchasing.requisitions.extension``); this module does
the same for sales until CCR P10b-1 adds an official hook:

* the part's ``migrations/`` directory joins the search path of the ``sales.migrations`` package, so Django's
  migration loader lists the part migrations as migrations of the ``sales`` app (names ``01xx_p10b_*``).
  Django *reloads* that package when it loads migrations (fresh module spec); :class:`_MigrationsFinder`
  (first on ``sys.meta_path``) returns the spec with the part directory included so it survives the reload;
* the part's ``models`` module is imported, so its models register under the ``sales`` app label.

``sales.urls`` (P10a) imports ``sales.portal.urls`` whenever the URL configuration loads (``migrate``,
``makemigrations --check``, ``runserver``, request processes); the part tests import the package at collection.
It is idempotent and harmless once the official hook is in place.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from django.apps import apps

_PART_DIR = Path(__file__).resolve().parent
_TARGET = "sales.migrations"
_EXTRA: list[str] = []


class _MigrationsFinder(importlib.abc.MetaPathFinder):
    """Finds ``sales.migrations`` like the default path finder, plus the P10b part directory."""

    def find_spec(
        self, fullname: str, path: Sequence[str] | None, target: ModuleType | None = None
    ) -> importlib.machinery.ModuleSpec | None:
        if fullname != _TARGET:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        if spec is not None and spec.submodule_search_locations is not None:
            for extra in _EXTRA:
                if extra not in spec.submodule_search_locations:
                    spec.submodule_search_locations.append(extra)
        return spec


def _finder_installed() -> bool:
    return any(isinstance(f, _MigrationsFinder) for f in sys.meta_path)


def install() -> None:
    extra = _PART_DIR / "migrations"
    if extra.is_dir() and str(extra) not in _EXTRA:
        _EXTRA.append(str(extra))
    if not _finder_installed():
        sys.meta_path.insert(0, _MigrationsFinder())
    core_migrations = importlib.import_module(_TARGET)
    for directory in _EXTRA:
        if directory not in list(core_migrations.__path__):
            core_migrations.__path__.append(directory)
    if apps.apps_ready:
        importlib.import_module("sales.portal.models")
