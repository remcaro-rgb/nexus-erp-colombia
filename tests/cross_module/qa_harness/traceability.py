"""``docs/traceability.csv`` generator (prompt P22 §11, shared/08).

Sources:
* user requirements UR-xx with their gates: the table in ``docs/prompt-pack/shared/08-traceability-matrix.md``;
* pytest tests marked ``@pytest.mark.req("UR-07", ...)`` (function, class, or module ``pytestmark``),
  found statically with ``ast`` under ``backend/`` and ``tests/``;
* Playwright tests whose title contains ``@UR-07`` (``*.spec.ts`` / ``*.spec.js``);
* outcomes: every JUnit XML under ``reports/junit/`` (+ ``reports/playwright/*.json``) joined by test name;
  status PASSED | FAILED | XFAIL (open P22 defect) | SKIPPED | NOT_RUN | MISSING (UR without any test) | UNKNOWN_UR.

Usage::

    python3 tests/cross_module/qa_harness/traceability.py               # write docs/traceability.csv
    python3 tests/cross_module/qa_harness/traceability.py --check       # exit 1 if any UR lacks a PASSED test
    python3 tests/cross_module/qa_harness/traceability.py --check --gate G2

Standard library only (runs without the project venv).
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SHARED_08 = ROOT / "docs" / "prompt-pack" / "shared" / "08-traceability-matrix.md"
OUT = ROOT / "docs" / "traceability.csv"
UR_RE = re.compile(r"UR-\d{2}")
SKIP_DIRS = {".venv", "node_modules", "__pycache__", ".git", "reports"}

HEADER = ("ur", "requirement", "gates", "kind", "test_file", "test_name", "status")


@dataclass(frozen=True, slots=True)
class Requirement:
    ur: str
    text: str
    gates: str


@dataclass(frozen=True, slots=True)
class TracedTest:
    ur: str
    kind: str  # pytest | playwright
    file: str  # repo-relative
    name: str  # function name (pytest) or title (playwright)
    junit_class: str  # expected JUnit classname prefix (pytest)


def parse_requirements(path: Path = SHARED_08) -> list[Requirement]:
    reqs: list[Requirement] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 6 and UR_RE.fullmatch(cells[0]):
            text = re.sub(r"\*\*", "", cells[1])
            reqs.append(Requirement(cells[0], text, cells[-1]))
    return reqs


def _req_args(node: ast.expr) -> list[str]:
    """URs in ``pytest.mark.req("UR-01", ...)`` (also ``mark.req``) or lists/tuples of those."""
    if isinstance(node, (ast.List, ast.Tuple)):
        return [u for elt in node.elts for u in _req_args(elt)]
    if not isinstance(node, ast.Call):
        return []
    fn = node.func
    name = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else "")
    if name != "req":
        return []
    out = []
    for arg in node.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            out += UR_RE.findall(arg.value)
    return out


def _iter_files(roots: Iterable[Path], patterns: tuple[str, ...]) -> Iterator[Path]:
    for root in roots:
        if not root.exists():
            continue
        for pat in patterns:
            for p in sorted(root.rglob(pat)):
                if not SKIP_DIRS.intersection(p.relative_to(ROOT).parts):
                    yield p


def scan_pytest(roots: Iterable[Path]) -> list[TracedTest]:
    found: list[TracedTest] = []
    for path in _iter_files(roots, ("test_*.py", "*_test.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        rel = path.relative_to(ROOT).as_posix()
        module_urs: list[str] = []
        for stmt in tree.body:
            if isinstance(stmt, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "pytestmark" for t in stmt.targets
            ):
                module_urs += _req_args(stmt.value)
        modname = rel[:-3].replace("/", ".")

        def visit(
            body: list[ast.stmt],
            cls_urs: list[str],
            cls_prefix: str,
            mod: str = modname,
            f: str = rel,
            mod_urs: tuple[str, ...] = tuple(module_urs),
        ) -> None:
            for node in body:
                if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                    urs = [u for d in node.decorator_list for u in _req_args(d)]
                    visit(node.body, cls_urs + urs, f"{cls_prefix}{node.name}.")
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                    urs = [u for d in node.decorator_list for u in _req_args(d)]
                    for ur in sorted(set(mod_urs) | set(cls_urs) | set(urs)):
                        found.append(
                            TracedTest(ur, "pytest", f, node.name, mod + ("." + cls_prefix[:-1] if cls_prefix else ""))
                        )

        visit(tree.body, [], "")
    return found


_PW_TEST_RE = re.compile(r"""\btest(?:\.(?:only|fixme|fail|slow))?\(\s*(['"`])(?P<title>.+?)\1""")


def scan_playwright(roots: Iterable[Path]) -> list[TracedTest]:
    found: list[TracedTest] = []
    for path in _iter_files(roots, ("*.spec.ts", "*.spec.js")):
        rel = path.relative_to(ROOT).as_posix()
        for m in _PW_TEST_RE.finditer(path.read_text(encoding="utf-8")):
            title = m.group("title")
            for ur in sorted(set(re.findall(r"@(UR-\d{2})", title))):
                found.append(TracedTest(ur, "playwright", rel, title, ""))
    return found


def junit_outcomes(report_dir: Path) -> dict[tuple[str, str], str]:
    """``(classname, base test name) -> PASSED|FAILED|SKIPPED`` (worst outcome wins)."""
    rank = {"PASSED": 0, "SKIPPED": 1, "XFAIL": 2, "FAILED": 3}
    out: dict[tuple[str, str], str] = {}
    for xml in sorted(report_dir.glob("*.xml")) if report_dir.exists() else []:
        try:
            tree = ET.parse(xml)  # noqa: S314 - local, self-generated reports
        except ET.ParseError:
            continue
        for tc in tree.iter("testcase"):
            cls = tc.get("classname", "")
            base = tc.get("name", "").split("[", 1)[0]
            if tc.find("failure") is not None or tc.find("error") is not None:
                st = "FAILED"
            elif tc.find("skipped") is not None:
                skipped = tc.find("skipped")
                st = "XFAIL" if skipped is not None and skipped.get("type") == "pytest.xfail" else "SKIPPED"
            else:
                st = "PASSED"
            key = (cls, base)
            if key not in out or rank[st] > rank[out[key]]:
                out[key] = st
    return out


def playwright_outcomes(report_dir: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for js in sorted(report_dir.glob("*.json")) if report_dir.exists() else []:
        try:
            data = json.loads(js.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        def walk(suite: dict[str, object]) -> None:
            for spec in suite.get("specs", []) or []:  # type: ignore[union-attr]
                title = str(spec.get("title", ""))  # type: ignore[union-attr]
                ok = bool(spec.get("ok", False))  # type: ignore[union-attr]
                out[title] = "PASSED" if ok and out.get(title) != "FAILED" else "FAILED"
            for sub in suite.get("suites", []) or []:  # type: ignore[union-attr]
                walk(sub)  # type: ignore[arg-type]

        for s in data.get("suites", []):
            walk(s)
    return out


def status_of(t: TracedTest, junit: dict[tuple[str, str], str], pw: dict[str, str]) -> str:
    if t.kind == "playwright":
        return pw.get(t.name, "NOT_RUN")
    # JUnit classnames are module paths relative to the rootdir (+ class); be tolerant of prefixes.
    hits = [
        st
        for (cls, name), st in junit.items()
        if name == t.name and (cls == t.junit_class or cls.endswith(t.junit_class))
    ]
    if not hits:
        return "NOT_RUN"
    for st in ("FAILED", "XFAIL", "PASSED"):
        if st in hits:
            return st
    return "SKIPPED"


def build_rows(
    reqs: list[Requirement], tests: list[TracedTest], junit: dict[tuple[str, str], str], pw: dict[str, str]
) -> list[tuple[str, ...]]:
    rows: list[tuple[str, ...]] = []
    by_ur: dict[str, list[TracedTest]] = {}
    for t in tests:
        by_ur.setdefault(t.ur, []).append(t)
    for r in reqs:
        ts = sorted(by_ur.get(r.ur, []), key=lambda t: (t.file, t.name))
        if not ts:
            rows.append((r.ur, r.text, r.gates, "", "", "", "MISSING"))
        for t in ts:
            rows.append((r.ur, r.text, r.gates, t.kind, t.file, t.name, status_of(t, junit, pw)))
    known = {r.ur for r in reqs}
    for t in sorted(tests, key=lambda t: (t.ur, t.file, t.name)):
        if t.ur not in known:
            rows.append((t.ur, "(not in shared/08)", "", t.kind, t.file, t.name, "UNKNOWN_UR"))
    return rows


def uncovered(rows: list[tuple[str, ...]], gate: str | None = None) -> list[str]:
    """URs (optionally only those due at ``gate``) without at least one PASSED test."""
    passed = {r[0] for r in rows if r[6] == "PASSED"}
    urs: dict[str, str] = {}
    for r in rows:
        if r[6] != "UNKNOWN_UR":
            urs[r[0]] = r[2]
    due = [u for u, gates in urs.items() if gate is None or re.search(rf"\b{re.escape(gate)}\b", gates)]
    return sorted(u for u in due if u not in passed)


def write_csv(rows: list[tuple[str, ...]], out: Path = OUT) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(HEADER)
        w.writerows(rows)
    return out


def generate(
    root: Path = ROOT, report_root: Path | None = None
) -> tuple[list[tuple[str, ...]], list[Requirement], list[TracedTest]]:
    reqs = parse_requirements(root / "docs" / "prompt-pack" / "shared" / "08-traceability-matrix.md")
    scan_roots = [root / "backend", root / "tests", root / "frontend"]
    tests = scan_pytest(scan_roots) + scan_playwright(scan_roots)
    reports = report_root or (root / "reports")
    rows = build_rows(reqs, tests, junit_outcomes(reports / "junit"), playwright_outcomes(reports / "playwright"))
    return rows, reqs, tests


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--reports", type=Path, default=None, help="reports root (default: reports/)")
    ap.add_argument("--check", action="store_true", help="fail if a UR has no PASSED test")
    ap.add_argument("--gate", default=None, help="with --check: only URs due at this gate (e.g. G2)")
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args(argv)
    rows, reqs, tests = generate(report_root=args.reports)
    if not args.no_write:
        write_csv(rows, args.out)
    missing = uncovered(rows, args.gate)
    sys.stdout.write(
        f"traceability: {len(reqs)} requirements, {len(tests)} traced tests, "
        f"{len(reqs) - len(uncovered(rows))} with a PASSED test; uncovered{f' at {args.gate}' if args.gate else ''}: "
        f"{' '.join(missing) or 'none'}\n"
    )
    if args.check and missing:
        sys.stdout.write("TRACEABILITY-FAILED\n")
        return 1
    sys.stdout.write("TRACEABILITY-OK\n" if args.check else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
