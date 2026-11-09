"""Gate evidence collector (prompt P22 "Required outputs per gate").

Copies machine-readable results from ``reports/`` into ``docs/gates/G<N>-evidence/<label>/`` and
writes ``index.json`` (file list with sha256 and size, JUnit totals, git commit, UTC time,
environment = dev, synthetic = true). Nothing secret is ever collected: files are filtered by
extension and scanned for secret-looking content; a hit aborts the collection.

Usage::

    python3 tests/cross_module/qa_harness/evidence.py --gate G2 --label integrated-head

Standard library only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET  # noqa: S405 - parses our own JUnit reports only
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
REPORTS = ROOT / "reports"
GATES = ROOT / "docs" / "gates"

# reports/<sub> -> evidence/<sub>
SECTIONS = {
    "junit": (".xml",),
    "coverage": (".xml",),
    "leak": (".csv", ".json"),
    "security": (".json", ".csv", ".md", ".sarif"),
    "perf": (".json", ".csv"),
    "a11y": (".json", ".md"),
    "scenarios": (".csv", ".json"),
    "playwright": (".json",),
    "traceability": (".csv",),
}
MAX_BYTES = 20 * 1024 * 1024
GATE_RE = re.compile(r"^G[0-4]$")
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)\b(password|passwd|client_secret|secret_key)\s*[=:]\s*['\"]?[^\s'\"<>]{8,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),  # JWT
)


class SecretFound(RuntimeError):
    pass


def scan_secrets(text: str) -> list[str]:
    return [p.pattern for p in SECRET_PATTERNS if p.search(text)]


def junit_totals(files: list[Path]) -> dict[str, int]:
    tot = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for f in files:
        try:
            root = ET.parse(f).getroot()  # noqa: S314 - local, self-generated reports
        except ET.ParseError:
            continue
        suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
        for s in suites:
            for k in tot:
                tot[k] += int(s.get(k, "0") or 0)
    return tot


def _git_sha(root: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


def collect(gate: str, label: str, source: Path = REPORTS, dest_root: Path = GATES, root: Path = ROOT) -> Path:
    if not GATE_RE.match(gate):
        raise ValueError(f"gate must be G0..G4, got {gate!r}")
    if not LABEL_RE.match(label):
        raise ValueError(f"invalid label {label!r}")
    dest = dest_root / f"{gate}-evidence" / label
    staged: list[tuple[Path, Path]] = []
    for section, exts in SECTIONS.items():
        src_dir = source / section
        if not src_dir.is_dir():
            continue
        for f in sorted(src_dir.rglob("*")):
            if f.is_file() and f.suffix.lower() in exts and f.stat().st_size <= MAX_BYTES:
                hits = scan_secrets(f.read_text(encoding="utf-8", errors="replace"))
                if hits:
                    raise SecretFound(f"{f}: secret-like content ({', '.join(hits)}); nothing collected")
                staged.append((f, dest / section / f.relative_to(src_dir)))
    if dest.exists():
        shutil.rmtree(dest)
    files: list[dict[str, Any]] = []
    for src, dst in staged:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        data = dst.read_bytes()
        files.append({"path": dst.relative_to(dest).as_posix(), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    dest.mkdir(parents=True, exist_ok=True)
    index = {
        "gate": gate,
        "label": label,
        "environment": "dev",
        "synthetic_data": True,
        "commit": _git_sha(root),
        "collected_at_utc": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "junit": junit_totals([dest / f["path"] for f in files if f["path"].startswith("junit/")]),
        "files": files,
        "note": "DEV build with synthetic data; not evidence of DIAN/UGPP/PILA or any legal compliance.",
    }
    (dest / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return dest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="collect gate evidence from reports/")
    ap.add_argument("--gate", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--source", type=Path, default=REPORTS)
    args = ap.parse_args(argv)
    try:
        dest = collect(args.gate, args.label, args.source)
    except (SecretFound, ValueError) as exc:
        sys.stdout.write(f"EVIDENCE-REFUSED: {exc}\n")
        return 1
    sys.stdout.write(f"EVIDENCE-OK {dest.relative_to(ROOT)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
