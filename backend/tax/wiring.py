"""Service registration at app ready (kernel.services) and demo-seed loaders.

* ``ParameterProvider`` → :class:`tax.parameters.store.DbParameterProvider`
* ``TaxService`` → :class:`tax.service.EngineTaxService`
* demo seed section ``tax`` (P21 data file): ``{parameters: true, synthetic_dev: true,
  exogena_synthetic_pack: true, companies: [<uuid>…]}``
"""

from __future__ import annotations

import uuid
from typing import Any

from kernel import seed, services
from kernel.interfaces.tax import TaxService
from kernel.parameters import ParameterProvider
from kernel.uow import UnitOfWork


def register_services() -> None:
    from tax.parameters.store import DbParameterProvider
    from tax.service import EngineTaxService

    if services.optional(ParameterProvider) is None:
        provider: ParameterProvider = DbParameterProvider()
        services.register(ParameterProvider, provider)
    if services.optional(TaxService) is None:
        tax_service: TaxService = EngineTaxService()
        services.register(TaxService, tax_service)
    seed.register_loader("tax", seed_tax, order=20)
    seed.register_loader("localization", seed_localization, order=10)


def seed_localization(uow: UnitOfWork, section: Any) -> int:
    from localization_co.loaders import load_defaults

    return sum(load_defaults().values()) if section else 0


def seed_tax(uow: UnitOfWork, section: Any) -> int:
    from tax.exogena.service import ensure_synthetic_pack
    from tax.parameters import seed as parameter_seed
    from tax.service import install_company_defaults, read_fixture

    section = section or {}
    count = 0
    if section.get("parameters", True):
        count += parameter_seed.load_defaults(uow)
    synthetic_dev = bool(section.get("synthetic_dev", False))
    if synthetic_dev:
        count += parameter_seed.load(
            uow, parameter_seed.read(parameter_seed.FIXTURES / "parameters_synthetic_dev.yaml")
        )
    companies = [uuid.UUID(str(c)) for c in section.get("companies", sorted(uow.ctx.company_ids))]
    for company_id in companies:
        data = read_fixture("tax_codes_default.yaml")
        if synthetic_dev:
            syn = read_fixture("parameters_synthetic_dev.yaml")
            data = {**data, "synthetic_concepts": syn.get("synthetic_concepts", []),
                    "synthetic_ica_tariffs": syn.get("synthetic_ica_tariffs", [])}  # fmt: skip
        count += sum(install_company_defaults(uow, company_id, data).values())
    if section.get("exogena_synthetic_pack", True):
        ensure_synthetic_pack(uow)
        count += 1
    return count
