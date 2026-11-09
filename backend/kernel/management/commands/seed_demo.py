"""``manage.py seed_demo`` — load the synthetic demo tenant (P21 fills the data)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from kernel import seed
from kernel.errors import DomainError


class Command(BaseCommand):
    help = "Load the synthetic demo tenant from backend/kernel/fixtures/demo_tenant.yaml."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--file", default=str(seed.DEFAULT_PATH))

    def handle(self, *args: Any, **options: Any) -> None:
        path = Path(options["file"])
        if not path.is_file():
            raise CommandError(f"demo tenant file not found: {path} (provided by P21)")
        try:
            counts = seed.load(path)
        except DomainError as exc:
            raise CommandError(str(exc)) from exc
        for section, n in counts.items():
            status = "no loader registered" if n < 0 else f"{n} rows"
            self.stdout.write(f"{section}: {status}")
        self.stdout.write(self.style.SUCCESS("seed_demo finished"))
