"""Extension bootstrap of the P11b parts (``requisitions``, ``portal``) inside the ``purchasing`` app (owner P11a).

P11b owns only ``backend/purchasing/requisitions/**`` and ``backend/purchasing/portal/**``; the app's
``models.py``, ``migrations/`` and ``apps.py`` belong to P11a. P09a solved the same split for inventory with an
official hook (``inventory/migrations/__init__.py`` appends the part directories to ``__path__``; ``models.py``
imports the part models). Until CCR P11b-1 adds that hook to purchasing, :func:`install` does the same from the
part's own package:

* the part's ``migrations/`` directory joins the search path of the ``purchasing.migrations`` package, so
  Django's migration loader lists the part migrations as migrations of the ``purchasing`` app (names
  ``01xx_p11b_*``). Django *reloads* that package when it loads migrations, which rebuilds ``__path__`` from a
  fresh module spec; :class:`_MigrationsFinder` (first on ``sys.meta_path``) therefore returns the spec with the
  part directories included, so the directories survive the reload;
* the part's ``models`` module is imported, so its models register under the ``purchasing`` app label.

When is it called? ``purchasing.urls`` (P11a) imports ``purchasing.requisitions.urls`` / ``purchasing.portal.urls``
whenever the URL configuration loads, i.e. in every ``manage.py`` command with system checks (``migrate``,
``makemigrations --check``, ``runserver``) and in every request-serving process; the part tests import it at
collection. It is idempotent and harmless once the official hook is in place.
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

_PACKAGE_DIR = Path(__file__).resolve().parents[1]
_TARGET = "purchasing.migrations"
_EXTRA: list[str] = []


class _MigrationsFinder(importlib.abc.MetaPathFinder):
    """Finds ``purchasing.migrations`` like the default path finder, plus the P11b part directories."""

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


def install(part: str) -> None:
    extra = _PACKAGE_DIR / part / "migrations"
    if extra.is_dir() and str(extra) not in _EXTRA:
        _EXTRA.append(str(extra))
    if not _finder_installed():
        sys.meta_path.insert(0, _MigrationsFinder())
    core_migrations = importlib.import_module(_TARGET)
    for directory in _EXTRA:
        if directory not in list(core_migrations.__path__):
            core_migrations.__path__.append(directory)
    if apps.apps_ready:
        importlib.import_module(f"purchasing.{part}.models")
