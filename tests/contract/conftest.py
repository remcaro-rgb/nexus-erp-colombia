"""Fixtures for the contract tests (P02). No services, no network."""
from __future__ import annotations

import os

import pytest

from contract_support import CONTRACTS, iter_operations, load_json, load_yaml


@pytest.fixture(scope="session")
def bundle():
    return load_json("contracts", "openapi", "openapi.json")


@pytest.fixture(scope="session")
def root_doc():
    return load_yaml("contracts", "openapi", "root.yaml")


@pytest.fixture(scope="session")
def fragments():
    d = os.path.join(CONTRACTS, "openapi", "modules")
    return {fn[:-5]: load_yaml("contracts", "openapi", "modules", fn) for fn in sorted(os.listdir(d)) if fn.endswith(".yaml")}


@pytest.fixture(scope="session")
def operations(bundle):
    return list(iter_operations(bundle))


@pytest.fixture(scope="session")
def errors_registry():
    return load_yaml("contracts", "errors.yaml")


@pytest.fixture(scope="session")
def catalog():
    return load_yaml("contracts", "permissions", "catalog.yaml")


@pytest.fixture(scope="session")
def feature_registry():
    return load_json("docs", "spec", "features", "feature-registry.json")
