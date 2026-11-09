#!/usr/bin/env python3
"""Render / verify the PII scrubbing statements of the OTel Collector config from pii-patterns.json. Owner: P03.

  render_otel_scrub.py           rewrite the block between the BEGIN/END markers in otel-collector.yaml
  render_otel_scrub.py --check   exit 1 when the config is out of sync with the pattern file (CI)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

OBS = Path(__file__).resolve().parents[2] / "deploy" / "observability"
CONFIG = OBS / "otel-collector.yaml"
BEGIN = "# BEGIN generated pii statements"
END = "# END generated pii statements"


def ottl_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def yaml_single(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def statements(context: str, indent: str) -> list[str]:
    data = json.loads((OBS / "pii-patterns.json").read_text(encoding="utf-8"))
    lines = []
    for p in data["patterns"]:
        rx, rep = ottl_string(p["regex"]), ottl_string(p["replacement"])
        targets = ["log.body"] if context == "log" else ["span.name"]
        targets += [f"{context}.attributes[{ottl_string(k)}]" for k in data["attribute_keys"]]
        for t in targets:
            lines.append(f"{indent}- {yaml_single(f'replace_pattern({t}, {rx}, {rep}) where IsString({t})')}")
    return lines


def render(text: str) -> str:
    out: list[str] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        if line.strip().startswith(BEGIN):
            context = line.strip()[len(BEGIN):].strip() or "log"
            indent = line[: len(line) - len(line.lstrip())]
            out.extend(statements(context, indent))
            while i < len(lines) and not lines[i].strip().startswith(END):
                i += 1
            out.append(lines[i])
        i += 1
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    current = CONFIG.read_text(encoding="utf-8")
    wanted = render(current)
    if "--check" in argv:
        if current != wanted:
            sys.stderr.write("otel-collector.yaml PII statements are out of sync; run scripts/ci/render_otel_scrub.py\n")
            return 1
        sys.stdout.write("otel scrub config in sync\n")
        return 0
    CONFIG.write_text(wanted, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
