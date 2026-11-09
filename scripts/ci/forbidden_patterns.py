#!/usr/bin/env python3
"""Forbidden-pattern scan (constitution C2-8, P03 deliverable 3). Owner: P03.

Backend Python code (backend/, mcp_server/) must not contain:
  TODO | FIXME | assert True | pytest.mark.skip without a registered BLK- blocker id | float( | bare except: | print(
Exit 1 with file:line list on any hit. Paths may be given as arguments (default: backend mcp_server).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RULES: list[tuple[str, re.Pattern[str]]] = [
    ("TODO", re.compile(r"\bTODO\b")),
    ("FIXME", re.compile(r"\bFIXME\b")),
    ("assert True", re.compile(r"\bassert\s+True\b")),
    ("skip without BLK- id", re.compile(r"(pytest\.mark\.skip(?:if)?|pytest\.skip\(|unittest\.skip)(?!.*BLK-\d+)")),
    ("float(", re.compile(r"(?<![\w.])float\(")),
    ("bare except:", re.compile(r"\bexcept\s*:")),
    ("print(", re.compile(r"(?<![\w.])print\(")),
]
SKIP_DIRS = {"__pycache__", ".venv", "node_modules", ".mypy_cache", ".pytest_cache"}


def iter_py(roots: list[Path]):
    for root in roots:
        if root.is_file() and root.suffix == ".py":
            yield root
        elif root.is_dir():
            for p in sorted(root.rglob("*.py")):
                if not SKIP_DIRS.intersection(p.parts):
                    yield p


def scan_text(text: str) -> list[tuple[int, str, str]]:
    hits = []
    for no, line in enumerate(text.splitlines(), 1):
        for name, rx in RULES:
            if rx.search(line):
                hits.append((no, name, line.strip()[:160]))
    return hits


def main(argv: list[str]) -> int:
    roots = [Path(a) if Path(a).is_absolute() else REPO / a for a in (argv[1:] or ["backend", "mcp_server"])]
    found = 0
    files = 0
    for path in iter_py(roots):
        files += 1
        for no, name, line in scan_text(path.read_text(encoding="utf-8", errors="replace")):
            found += 1
            sys.stderr.write(f"{path.relative_to(REPO) if path.is_relative_to(REPO) else path}:{no}: forbidden {name}: {line}\n")
    if found:
        sys.stderr.write(f"forbidden-pattern scan FAILED: {found} hit(s) in {files} file(s)\n")
        return 1
    sys.stdout.write(f"forbidden-pattern scan OK ({files} files)\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
