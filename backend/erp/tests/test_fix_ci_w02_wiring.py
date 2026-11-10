"""FIX-CI-W02 (UI-1): first-party app discovery, route mounting and RLS coverage of the wired apps."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.apps import apps
from django.conf import settings
from django.urls import resolve

from erp import discovery


def _pkg(root: Path, name: str, *, apps_py: bool = True, init: bool = True) -> None:
    d = root / name
    d.mkdir()
    if init:
        (d / "__init__.py").write_text("")
    if apps_py:
        (d / "apps.py").write_text("")


def test_FIX_CI_W02_discovery_orders_kernel_iam_then_sorted(tmp_path: Path) -> None:
    for name in ("workflow", "ledger", "iam", "tax", "kernel", "localization_co"):
        _pkg(tmp_path, name)
    _pkg(tmp_path, "erp", apps_py=False)  # project package: not an app
    _pkg(tmp_path, "scratch", init=False)  # not a package
    (tmp_path / "docs").mkdir()
    assert discovery.discover_apps(tmp_path) == ["kernel", "iam", "ledger", "localization_co", "tax", "workflow"]


def test_FIX_CI_W02_settings_install_every_backend_app() -> None:
    backend = Path(settings.BASE_DIR)
    expected = discovery.discover_apps(backend)
    assert {"kernel", "iam", "localization_co", "tax", "workflow"} <= set(expected)
    assert expected == settings.ERP_LOCAL_APPS
    assert settings.INSTALLED_APPS[-len(expected) :] == expected
    for app in expected:
        assert apps.is_installed(app), app


@pytest.mark.parametrize(
    ("url", "view_module"),
    [
        ("/api/v1/iam/me", "iam.api.views"),
        ("/api/portal/v1/me", "iam.external.views"),
        ("/api/v1/tax/parameters", "tax.api.views"),
        ("/api/v1/workflow/formulas", "workflow.api"),
        ("/api/v1/kpis/aprobaciones", "workflow.api"),
        ("/api/health/live", "kernel.views"),
    ],
)
def test_FIX_CI_W02_routes_mounted(url: str, view_module: str) -> None:
    match = resolve(url)
    func = getattr(match.func, "view_class", match.func)
    assert func.__module__ == view_module


def test_FIX_CI_W02_mount_rule_default_and_declared() -> None:
    by_label = {c.label: c for c in apps.get_app_configs()}
    patterns = {str(p.pattern) for p in discovery.app_url_patterns(by_label.values(), settings.ERP_LOCAL_APPS)}
    assert {"", "api/v1/tax/", "api/v1/workflow/", "api/v1/kpis/"} <= patterns
    assert not any(p.startswith("api/v1/kernel") for p in patterns)  # kernel routes are explicit
    assert not any(p.startswith("api/v1/localization_co") for p in patterns)  # no urls.py → nothing mounted
    assert discovery.default_mount("ledger") == "api/v1/ledger/"


@pytest.mark.django_db
def test_FIX_CI_W02_every_tenant_table_of_the_wired_apps_has_forced_rls() -> None:
    from iam import rls

    missing = [t for p in ("tax_", "workflow_", "localization_co_") for t in rls.tables_without_rls(p)]
    assert missing == []
