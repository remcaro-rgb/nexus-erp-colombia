"""Adapter registry: adapters are plugged in by key, without changing the gateway (F-PT-OPEN pluggability)."""

from __future__ import annotations

from collections.abc import Callable

from einvoice.adapters.base import AdapterConfig, ProviderAdapter
from einvoice.errors import ProviderNotConfigured

AdapterFactory = Callable[[AdapterConfig], ProviderAdapter]

_factories: dict[str, AdapterFactory] = {}


def register_adapter(key: str, factory: AdapterFactory) -> None:
    if not key or not key.replace("_", "").replace("-", "").isalnum():
        raise ValueError(f"invalid adapter key {key!r}")
    _factories[key] = factory


def unregister_adapter(key: str) -> None:
    _factories.pop(key, None)


def keys() -> list[str]:
    return sorted(_factories)


def create(config: AdapterConfig) -> ProviderAdapter:
    factory = _factories.get(config.adapter)
    if factory is None:
        raise ProviderNotConfigured(f"no adapter registered under {config.adapter!r}")
    return factory(config)


def register_builtins() -> None:
    from einvoice.adapters.direct import DirectDianAdapter
    from einvoice.adapters.http_provider import HttpProviderAdapter
    from einvoice.adapters.simulator import SandboxSimulator

    register_adapter("simulator", SandboxSimulator)
    register_adapter("http_generic", HttpProviderAdapter)
    register_adapter("direct", DirectDianAdapter)
