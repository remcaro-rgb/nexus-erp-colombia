"""``manage.py einvoice_worker --tenant <uuid> --company <uuid> [--once]`` — process due fiscal work.

Signs and transmits built documents, due retries, the contingency backlog and pending RADIAN events for one
company, as a service principal on the ``job`` channel (outside any unit of work). The outbox consumer
``einvoice.transmit`` handles new documents as they are built; this command is the sweeper.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from einvoice import worker

SERVICE_PRINCIPAL = uuid.UUID("00000000-0000-7000-8000-0000000e1001")


class Command(BaseCommand):
    help = "Process due e-invoicing work (transmissions, retries, contingency backlog, RADIAN events)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--tenant", required=True, type=uuid.UUID)
        parser.add_argument("--company", required=True, type=uuid.UUID)
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--interval", type=int, default=60)

    def handle(self, *args: Any, **opts: Any) -> None:
        ctx = worker.worker_context(opts["tenant"], opts["company"], SERVICE_PRINCIPAL)
        while True:
            counts = worker.process_due(ctx, opts["company"])
            self.stdout.write(f"einvoice_worker: {sorted(counts.items())}")
            if opts["once"]:
                return
            time.sleep(max(5, int(opts["interval"])))
