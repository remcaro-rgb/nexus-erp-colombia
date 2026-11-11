"""Registers ``IntercompanyService`` in ``kernel.services`` (first registration wins)."""

from __future__ import annotations

from typing import Any

from kernel import services


def implementations() -> dict[Any, object]:
    from consolidation.intercompany import DbIntercompanyService
    from consolidation.interface import IntercompanyService

    return {IntercompanyService: DbIntercompanyService()}


def register_services() -> None:
    for interface, impl in implementations().items():
        if services.optional(interface) is None:
            services.register(interface, impl)
