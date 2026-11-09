"""Forbid floats in money paths (P01 deliverable 2, C2-2).

Rules over every ``*.py`` under the given roots:

* ``ERP001`` — a call to the ``float`` builtin;
* ``ERP002`` — any reference to ``FloatField`` (Django or DRF);
* ``ERP003`` — a float literal assigned to / compared with a name that looks
  money-typed (amount, price, cost, total, qty, rate, tax, balance…).

A line may opt out with the marker ``# erp: allow-float`` followed by the reason in
parentheses (reviewed by the integrator). Note: the C2-8 forbidden-pattern scan
(scripts/ci/forbidden_patterns.py) also rejects that marker text in backend code,
so in practice only code outside its roots can use it. Usage: ``python -m kernel.lint.no_float backend``.
"""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

MONEY_NAME = re.compile(
    r"(amount|price|cost|total|subtotal|qty|quantity|rate|tax|vat|iva|balance|debit|credit|money|valor|precio|monto|saldo)",
    re.IGNORECASE,
)
ALLOW = re.compile(r"#\s*erp:\s*allow-float\(.+\)")
SKIP_DIRS = {".venv", "node_modules", "__pycache__", "migrations"}


@dataclass(frozen=True, slots=True)
class Violation:
    path: Path
    line: int
    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.code} {self.message}"


def _target_names(node: ast.AST) -> Iterator[str]:
    if isinstance(node, ast.Name):
        yield node.id
    elif isinstance(node, ast.Attribute):
        yield node.attr
    elif isinstance(node, ast.Tuple | ast.List):
        for elt in node.elts:
            yield from _target_names(elt)


def _is_float_literal(node: ast.AST | None) -> bool:
    if isinstance(node, ast.UnaryOp):
        node = node.operand
    return isinstance(node, ast.Constant) and isinstance(node.value, float)


def check_source(source: str, path: Path) -> list[Violation]:
    tree = ast.parse(source, filename=str(path))
    lines = source.splitlines()
    out: list[Violation] = []

    def add(node: ast.AST, code: str, msg: str) -> None:
        lineno = getattr(node, "lineno", 1)
        text = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
        if not ALLOW.search(text):
            out.append(Violation(path, lineno, code, msg))

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "float":
            add(node, "ERP001", "calling the float builtin is forbidden; use Decimal")
        elif (isinstance(node, ast.Attribute) and node.attr == "FloatField") or (
            isinstance(node, ast.Name) and node.id == "FloatField"
        ):
            add(node, "ERP002", "FloatField is forbidden; use kernel.fields")
        elif isinstance(node, ast.ImportFrom) and any(a.name == "FloatField" for a in node.names):
            add(node, "ERP002", "FloatField import is forbidden")
        elif isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [n for t in targets for n in _target_names(t)]
            if _is_float_literal(node.value) and any(MONEY_NAME.search(n) for n in names):
                add(node, "ERP003", "float literal assigned to a money-typed name")
        elif (
            isinstance(node, ast.keyword) and node.arg and MONEY_NAME.search(node.arg) and _is_float_literal(node.value)
        ):
            add(node, "ERP003", "float literal passed as a money-typed argument")
    return out


def iter_files(roots: list[Path]) -> Iterator[Path]:
    for root in roots:
        if root.is_file():
            yield root
            continue
        for path in sorted(root.rglob("*.py")):
            if not SKIP_DIRS.intersection(path.parts):
                yield path


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    roots = [Path(a) for a in (args or ["backend"])]
    violations: list[Violation] = []
    for path in iter_files(roots):
        violations.extend(check_source(path.read_text(encoding="utf-8"), path))
    for v in violations:
        sys.stderr.write(f"{v}\n")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
