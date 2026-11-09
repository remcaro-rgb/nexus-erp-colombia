"""``manage.py import_divipola [--file dane.csv|sample.yaml]`` — load DANE DIVIPOLA.

CSV columns (DANE export): ``departamento_codigo, departamento, municipio_codigo, municipio``.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction

from localization_co import loaders


class Command(BaseCommand):
    help = "Import DANE DIVIPOLA departments and municipalities (CSV or YAML); default: bundled sample"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--file", default="")

    def handle(self, *args: Any, **options: Any) -> None:
        path = Path(options["file"]) if options["file"] else loaders.FIXTURES / "divipola_sample.yaml"
        if path.suffix.lower() == ".csv":
            deps: dict[str, str] = {}
            muns: list[dict[str, str]] = []
            with path.open(encoding="utf-8-sig", newline="") as fh:
                for row in csv.DictReader(fh):
                    dep = row["departamento_codigo"].strip().zfill(2)
                    deps[dep] = row["departamento"].strip()
                    muns.append(
                        {
                            "code": row["municipio_codigo"].strip().zfill(5),
                            "name": row["municipio"].strip(),
                            "department": dep,
                        }
                    )
            data: dict[str, Any] = {
                "departments": [{"code": c, "name": n} for c, n in sorted(deps.items())],
                "municipalities": muns,
            }
            source = f"DANE DIVIPOLA file {path.name}"
        else:
            data = loaders.load_yaml(path)
            source = ""
        with transaction.atomic():
            d, m = loaders.load_divipola(data, source)
        self.stdout.write(f"divipola: {d} departments, {m} municipalities")
