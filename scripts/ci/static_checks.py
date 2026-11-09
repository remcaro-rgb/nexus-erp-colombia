#!/usr/bin/env python3
"""Architecture static checks required by ADR-004 (P03 deliverable 4). Owner: P03.

  SEC-VIEW   every DRF view class inherits SecuredViewSet / SecuredAPIView (no direct APIView/ViewSet/generics
             subclass, no @api_view function views) outside the modules that define the secured bases
  RAW-SQL    no raw SQL (cursor()/execute()/.raw()/RawSQL/.extra()) outside the allow-listed modules
             (iam/rls, reporting/compiler, migrations, kernel tenancy binding) and tests
  HTTP-LIB   no `requests` / `httpx` import outside adapters (…/adapters/…, adapters.py) and tests
Allow-list: scripts/ci/static_checks_allowlist.json (each entry has a reason). Exit 1 on any violation.
"""
from __future__ import annotations

import ast
import fnmatch
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ALLOWLIST = json.loads((Path(__file__).with_name("static_checks_allowlist.json")).read_text(encoding="utf-8"))
DRF_BASES = {
    "APIView", "GenericAPIView", "ViewSet", "GenericViewSet", "ModelViewSet", "ReadOnlyModelViewSet",
    "ViewSetMixin", "ListAPIView", "CreateAPIView", "RetrieveAPIView", "UpdateAPIView", "DestroyAPIView",
    "ListCreateAPIView", "RetrieveUpdateAPIView", "RetrieveDestroyAPIView", "RetrieveUpdateDestroyAPIView",
}
SECURED = {"SecuredViewSet", "SecuredAPIView"}
RAW_CALLS = {"cursor", "execute", "executemany", "raw", "extra", "RawSQL"}
HTTP_LIBS = {"requests", "httpx"}


def matches(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pat) for pat in patterns)


def is_test(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return "/tests/" in f"/{path}" or name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"


def base_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return base_name(node.value)
    return ""


def check_file(path: Path, rel: str) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=rel)
    except SyntaxError as exc:
        return [f"{rel}:{exc.lineno}: SYNTAX cannot parse ({exc.msg})"]
    out: list[str] = []
    test = is_test(rel)
    view_ok = matches(rel, ALLOWLIST["secured_base_modules"]["paths"])
    sql_ok = test or matches(rel, ALLOWLIST["raw_sql"]["paths"])
    http_ok = test or matches(rel, ALLOWLIST["http_clients"]["paths"])
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and not view_ok and not test:
            bases = {base_name(b) for b in node.bases}
            if bases & DRF_BASES and node.name not in SECURED:
                out.append(f"{rel}:{node.lineno}: SEC-VIEW class {node.name} inherits {sorted(bases & DRF_BASES)}; "
                           "inherit SecuredViewSet/SecuredAPIView")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not view_ok and not test:
            for dec in node.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                if base_name(target) == "api_view":
                    out.append(f"{rel}:{node.lineno}: SEC-VIEW function view {node.name} uses @api_view")
        if isinstance(node, ast.Call) and not sql_ok:
            name = base_name(node.func)
            if name in RAW_CALLS and (name == "RawSQL" or isinstance(node.func, ast.Attribute)):
                if name == "execute" and isinstance(node.func, ast.Attribute) and base_name(node.func.value) not in (
                        "cursor", "cur", "c", "conn", "connection"):
                    continue
                out.append(f"{rel}:{node.lineno}: RAW-SQL call .{name}() outside the allow-listed modules")
        if isinstance(node, (ast.Import, ast.ImportFrom)) and not http_ok:
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for mod in mods:
                if mod.split(".")[0] in HTTP_LIBS:
                    out.append(f"{rel}:{node.lineno}: HTTP-LIB import of {mod} outside an adapter module")
    return out


def main(argv: list[str]) -> int:
    roots = [REPO / a for a in (argv[1:] or ["backend", "mcp_server"])]
    violations: list[str] = []
    files = 0
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts or ".venv" in path.parts:
                continue
            files += 1
            violations += check_file(path, str(path.relative_to(REPO)))
    if violations:
        sys.stderr.write("\n".join(violations) + f"\nstatic checks FAILED: {len(violations)} violation(s)\n")
        return 1
    sys.stdout.write(f"static checks OK ({files} files)\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
