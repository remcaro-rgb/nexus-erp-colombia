#!/usr/bin/env python3
"""Reference PII scrubber built from deploy/observability/pii-patterns.json (T-P03-04). Owner: P03.

The same pattern file drives the OpenTelemetry Collector `transform/scrub_pii` processor
(deploy/observability/otel-collector.yaml, kept in sync by scripts/ci/render_otel_scrub.py) and is offered to the
backend logging filter (CCR P03-1). Usage: echo "text" | pii_scrub.py
"""
from __future__ import annotations

import json
import re
import sys
from functools import lru_cache
from pathlib import Path

PATTERNS_FILE = Path(__file__).resolve().parents[2] / "deploy" / "observability" / "pii-patterns.json"


@lru_cache(maxsize=1)
def compiled() -> tuple[tuple[re.Pattern[str], str], ...]:
    data = json.loads(PATTERNS_FILE.read_text(encoding="utf-8"))
    return tuple((re.compile(p["regex"]), p["replacement"]) for p in data["patterns"])


def scrub(text: str) -> str:
    for rx, replacement in compiled():
        text = rx.sub(replacement, text)
    return text


if __name__ == "__main__":
    sys.stdout.write(scrub(sys.stdin.read()))
