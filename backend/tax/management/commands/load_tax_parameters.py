"""``manage.py load_tax_parameters --tenant <uuid> --principal <uuid> [--company <uuid>…] [--synthetic-dev]``.

Loads the 2026 parameter seed (statuses per shared/04), the default tax codes and
the synthetic exógena pack for one tenant, as a service principal, in one unit of work.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from kernel.tenancy import Channel, PrincipalType, TenantContext
from kernel.uow import run_in_unit_of_work
from tax.wiring import seed_tax


class Command(BaseCommand):
    help = "Load tax parameters, default tax codes and the synthetic exógena pack into a tenant"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--tenant", required=True)
        parser.add_argument("--principal", required=True)
        parser.add_argument("--company", action="append", default=[])
        parser.add_argument("--synthetic-dev", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        companies = [uuid.UUID(c) for c in options["company"]]
        ctx = TenantContext(
            tenant_id=uuid.UUID(options["tenant"]),
            company_ids=frozenset(companies),
            principal_id=uuid.UUID(options["principal"]),
            principal_type=PrincipalType.SERVICE,
            channel=Channel.IMPORT,
        )
        section = {"companies": [str(c) for c in companies], "synthetic_dev": options["synthetic_dev"]}
        count = run_in_unit_of_work(lambda uow: seed_tax(uow, section), ctx)
        self.stdout.write(f"tax: {count} rows")
