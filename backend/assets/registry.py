"""Registers the fixed-assets implementations in ``kernel.services`` (first registration wins)."""

from __future__ import annotations

from typing import Any

from kernel import services


def implementations() -> dict[Any, object]:
    from assets.interface import AssetService
    from assets.service import DbAssetService

    return {AssetService: DbAssetService()}


def register_services() -> None:
    for interface, impl in implementations().items():
        if services.optional(interface) is None:
            services.register(interface, impl)
