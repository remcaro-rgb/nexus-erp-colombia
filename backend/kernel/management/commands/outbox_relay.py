"""``manage.py outbox_relay`` — publish pending outbox rows (ADR-006)."""

from __future__ import annotations

import time
from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from kernel import outbox


class Command(BaseCommand):
    help = "Relay kernel_outbox rows to Celery (FOR UPDATE SKIP LOCKED batches)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--once", action="store_true", help="process one batch and exit")
        parser.add_argument("--batch-size", type=int, default=100)
        parser.add_argument("--idle-sleep", type=int, default=1, help="seconds between empty polls")
        parser.add_argument("--max-batches", type=int, default=0, help="0 = run until stopped")

    def handle(self, *args: Any, **options: Any) -> None:
        batches = 0
        while True:
            result = outbox.relay_batch(batch_size=options["batch_size"])
            batches += 1
            self.stdout.write(f"published={result.published} failed={result.failed} dead={result.dead}")
            if options["once"] or (options["max_batches"] and batches >= options["max_batches"]):
                return
            if result.published == 0:
                time.sleep(options["idle_sleep"])
