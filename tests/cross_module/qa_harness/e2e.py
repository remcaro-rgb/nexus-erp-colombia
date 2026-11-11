"""Runner of the P22c feature e2e specs (``tests/e2e/features/*.spec.ts``), DEV/test only.

One pytest case per mandatory UI test id (``tests/cross_module/test_xm_e2e_features.py``):

1. the case builds a synthetic world in the real PostgreSQL test database (``transactional_db``: rows are committed so
   the server thread sees them) with the module kits' real services (policy engine, inventory, ledger, purchasing,
   treasury, payroll …) and local OIDC bearer tokens (``iam.oidc.local.LocalIssuer``);
2. :func:`live_api` serves the real Django WSGI application on ``127.0.0.1:<ephemeral>`` (threaded wsgiref, every
   request's DB connection closed afterwards so the test database can be flushed and dropped);
3. :func:`run_specs` writes the world file (temp dir, 0600; tokens are synthetic and never printed), runs Playwright
   with the system Chrome on ``tests/e2e/features/playwright.config.ts`` filtered by the test id and asserts that every
   collected spec passed. The Playwright JSON report is copied to ``reports/playwright/p22c-<ID>.json`` as evidence.

Nothing is skipped: a missing prerequisite (pnpm, installed JS deps, ``$CHROME_PATH``) fails the case with the reason.
"""

from __future__ import annotations

import json
import os
import shutil
import socketserver
import subprocess
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import paths

ROOT = paths.ROOT
FRONTEND = ROOT / "frontend"
CONFIG = ROOT / "tests" / "e2e" / "features" / "playwright.config.ts"
EVIDENCE = paths.REPORTS / "playwright"


def missing_prerequisite() -> str | None:
    if not (ROOT / "package.json").is_file() or not (FRONTEND / "vite.config.ts").is_file():
        return "frontend workspace (package.json, frontend/vite.config.ts) not present"
    if not (ROOT / "node_modules" / "@playwright" / "test").exists():
        return "JS dependencies not installed (run `pnpm install --frozen-lockfile`)"
    if shutil.which("pnpm") is None:
        return "pnpm not on PATH"
    chrome = os.environ.get("CHROME_PATH", "")
    if not chrome or not Path(chrome).exists():
        return "system Chrome ($CHROME_PATH) not available"
    return None


@contextmanager
def live_api() -> Iterator[str]:
    """The real Django WSGI app on a loopback ephemeral port (never 0.0.0.0, D-002)."""
    from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

    from django.core.wsgi import get_wsgi_application
    from django.db import connections

    django_app = get_wsgi_application()

    def app(environ: dict[str, Any], start_response: Any) -> Any:
        result = django_app(environ, start_response)
        try:
            body = b"".join(result)
        finally:
            close = getattr(result, "close", None)
            if close is not None:
                close()  # request_finished
            connections.close_all()  # thread-local connections of this request thread
        return [body]

    class Quiet(WSGIRequestHandler):
        def log_message(self, *args: Any) -> None:
            return None

    class Threaded(socketserver.ThreadingMixIn, WSGIServer):
        daemon_threads = True

    srv = make_server("127.0.0.1", 0, app, server_class=Threaded, handler_class=Quiet)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


GAP_ERROR = "Error: acceptance steps without a UI"


def _walk(suite: dict[str, Any], prefix: str, out: list[tuple[str, dict[str, Any]]]) -> None:
    for spec in suite.get("specs", []):
        out.append((f"{prefix} {spec.get('title', '')}".strip(), spec))
    for child in suite.get("suites", []):
        _walk(child, f"{prefix} {child.get('title', '')}".strip(), out)


def _outcome(spec: dict[str, Any]) -> tuple[bool, list[str], list[str]]:
    """(passed, error messages of the last attempt, recorded gaps)."""
    tests = spec.get("tests") or []
    passed = bool(tests) and bool(spec.get("ok"))
    errors: list[str] = []
    gaps: list[str] = []
    for t in tests:
        results = t.get("results") or []
        last = results[-1] if results else {}
        if last.get("status") != "passed":
            passed = False
        errors += [str(e.get("message") or e.get("value") or "") for e in last.get("errors") or []]
        gaps += [str(a.get("description", "")) for a in t.get("annotations") or [] if a.get("type") == "gap"]
    return passed, errors, gaps


def run_specs(
    test_id: str, world: dict[str, Any], *, expected: int, known_gaps: tuple[str, ...] = (), timeout: int = 900
) -> list[str]:
    """Run the specs whose title contains ``test_id`` and judge them.

    * every spec passed and no ``known_gaps`` -> returns the titles;
    * the only failure is the gap check and the recorded gaps are exactly ``known_gaps`` (registered defects of a
      missing UI step) -> ``pytest.xfail`` naming them (reported as not passed; never PASSED);
    * anything else fails, including a known gap that disappeared (strict: the defect id must be removed).
    """
    import pytest

    reason = missing_prerequisite()
    assert reason is None, f"{test_id}: e2e prerequisite missing: {reason}"
    pnpm = shutil.which("pnpm")
    assert pnpm is not None
    with tempfile.TemporaryDirectory(prefix=f"p22c-{test_id}-") as tmp:
        world_file = Path(tmp) / "world.json"
        fd = os.open(world_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(world, fh, default=str)
        report = Path(tmp) / "results.json"
        env = {
            **os.environ,
            "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1",
            "PLAYWRIGHT_JSON_OUTPUT_NAME": str(report),
            "NEXUS_E2E_WORLD": str(world_file),
            # one artifacts folder per test id: Playwright wipes its outputDir at start (parallel runs must not collide)
            "NEXUS_E2E_OUTPUT_DIR": str(EVIDENCE / "p22c-artifacts" / test_id),
            "CI": "1",
        }
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [pnpm, "--dir", str(FRONTEND), "exec", "playwright", "test", "--config", str(CONFIG), "--grep", test_id],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        output = (proc.stdout + proc.stderr)[-12000:]
        assert report.is_file(), f"Playwright wrote no JSON report (exit {proc.returncode}):\n{output}"
        data = json.loads(report.read_text(encoding="utf-8"))
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(report, EVIDENCE / f"p22c-{test_id}.json")
    assert not data.get("errors"), f"Playwright reported errors: {data['errors']}\n{output}"
    specs: list[tuple[str, dict[str, Any]]] = []
    for suite in data.get("suites", []):
        _walk(suite, suite.get("title", ""), specs)
    ran = [(t, s) for t, s in specs if test_id in t]
    titles = [t for t, _ in ran]
    assert len(ran) == expected, f"{test_id}: expected {expected} specs, Playwright collected {titles}\n{output}"
    outcomes = {t: _outcome(s) for t, s in ran}
    if all(ok for ok, _e, _g in outcomes.values()):
        assert not known_gaps, f"{test_id}: passes without the registered gaps {known_gaps}: remove them (strict)"
        assert proc.returncode == 0, f"Playwright exit {proc.returncode}\n{output}"
        return titles
    only_gaps = all(ok or (errors and all(e.startswith(GAP_ERROR) for e in errors)) for ok, errors, _g in outcomes.values())
    recorded = sorted({g.split(":", 1)[0] for _ok, _e, gaps in outcomes.values() for g in gaps})
    if known_gaps and only_gaps and recorded == sorted(set(known_gaps)):
        detail = sorted({g for _ok, _e, gaps in outcomes.values() for g in gaps})
        pytest.xfail(f"{test_id}: every UI step passed except the registered gaps: {'; '.join(detail)}")
    failed = [t for t, (ok, _e, _g) in outcomes.items() if not ok]
    raise AssertionError(f"{test_id}: failed specs {failed} (gaps recorded {recorded}, registered {list(known_gaps)})\n{output}")


def tokens(w: Any, *users: str, company: str = "ALFA") -> dict[str, str]:
    """LocalIssuer bearers for synthetic users of a kit world; each also gets the e2e base role (own /iam/me)."""
    if "e2e_base" not in w.world.roles:
        w.world.add_role("e2e_base", ["iam.me.read"])
    for u in users:
        w.world.assign(u, "e2e_base", company)
    return {u: str(w.issuer.issue(str(w.world.users[u].idp_subject), w.world.tenant_id, "erp-web")) for u in users}


def run_feature(
    test_id: str,
    build: Any,
    *,
    expected: int = 1,
    augment: bool = False,
    known_gaps: tuple[str, ...] = (),
    world: Any = None,
) -> None:
    """Build the world (default: tests/gates/g2_world.gate_world), serve the API, run and judge the specs."""
    import sys

    gates = str(ROOT / "tests" / "gates")
    if gates not in sys.path:
        sys.path.insert(0, gates)
    if world is None:
        from g2_world import gate_world

        world = gate_world
    with world() as w, live_api() as origin:
        payload = build(w)
        payload["api_origin"] = origin
        payload["augment"] = augment
        run_specs(test_id, payload, expected=expected, known_gaps=known_gaps)
