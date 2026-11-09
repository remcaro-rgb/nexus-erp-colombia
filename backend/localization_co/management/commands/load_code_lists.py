"""``manage.py load_code_lists [--file lists.yaml]`` — load versioned DIAN code lists."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction

from localization_co import loaders


class Command(BaseCommand):
    help = "Load DIAN annex code lists as versioned data (default: synthetic placeholders)"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--file", default="")

    def handle(self, *args: Any, **options: Any) -> None:
        path = Path(options["file"]) if options["file"] else loaders.FIXTURES / "code_lists.yaml"
        with transaction.atomic():
            count = loaders.load_code_lists(loaders.load_yaml(path))
        self.stdout.write(f"code lists: {count} versions")
