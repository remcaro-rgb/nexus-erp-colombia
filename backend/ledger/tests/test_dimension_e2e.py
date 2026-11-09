"""T-ACC-DIM-08 (e2e, gate G1) — runner for the Playwright spec of the dimension masters UI.

The browser test itself is ``frontend/src/features/ledger/dimensions/e2e/T-ACC-DIM-08.dimensions.spec.ts`` (table<->tree
toggle, create group+leaf, move, archive in each of the 4 tabs; keyboard navigation of the role=tree). This pytest case
runs it with its own Playwright config (``.../e2e/playwright.config.ts``) and system Chrome, so the result does not
depend on what a config-less ``playwright test`` of the whole frontend tree collects: other modules' vitest files
(``*.test.ts``) make that collection fail before any test runs (CCR P05-5).

It is skipped (never passed) when the JS workspace of P08 (root ``package.json``, ``frontend/vite.config.ts``,
installed ``@playwright/test``) or ``$CHROME_PATH`` is absent; a skip is not PASSED for featctl.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
FRONTEND = ROOT / "frontend"
CONFIG = "src/features/ledger/dimensions/e2e/playwright.config.ts"
EXPECTED_TESTS = 5  # 4 tabs + keyboard navigation


def _pnpm() -> str | None:
    return shutil.which("pnpm")


def _missing_prerequisite() -> str | None:
    if not (ROOT / "package.json").is_file() or not (FRONTEND / "vite.config.ts").is_file():
        return "frontend workspace (P08: package.json, frontend/vite.config.ts) not present"
    if not (FRONTEND / "node_modules" / "@playwright" / "test").exists():
        return "JS dependencies not installed (run `pnpm install --frozen-lockfile`)"
    if _pnpm() is None:
        return "pnpm not on PATH"
    chrome = os.environ.get("CHROME_PATH", "")
    if not chrome or not Path(chrome).exists():
        return "system Chrome ($CHROME_PATH) not available"
    return None


def _walk_specs(suite: dict, out: list[dict]) -> None:
    for spec in suite.get("specs", []):
        out.append(spec)
    for child in suite.get("suites", []):
        _walk_specs(child, out)


def test_T_ACC_DIM_08_dimensions_ui_playwright() -> None:
    reason = _missing_prerequisite()
    if reason:
        pytest.skip(f"T-ACC-DIM-08 not run: {reason}")

    pnpm = _pnpm()
    assert pnpm is not None
    with tempfile.TemporaryDirectory(prefix="p05-dim08-") as tmp:
        report = Path(tmp) / "results.json"
        env = {
            **os.environ,
            "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1",
            "PLAYWRIGHT_JSON_OUTPUT_NAME": str(report),
            "CI": "1",
        }
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [pnpm, "--dir", str(FRONTEND), "exec", "playwright", "test", "--config", CONFIG, "--reporter=list,json"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        output = (proc.stdout + proc.stderr)[-6000:]
        assert report.is_file(), f"Playwright wrote no JSON report (exit {proc.returncode}):\n{output}"
        data = json.loads(report.read_text(encoding="utf-8"))

    assert not data.get("errors"), f"Playwright reported errors: {data['errors']}\n{output}"
    specs: list[dict] = []
    for suite in data.get("suites", []):
        _walk_specs(suite, specs)
    titles = [s["title"] for s in specs]
    assert len(specs) == EXPECTED_TESTS, f"expected {EXPECTED_TESTS} T-ACC-DIM-08 cases, got {titles}\n{output}"
    assert all("T-ACC-DIM-08" in t for t in titles), titles
    not_ok = [s["title"] for s in specs if not s.get("ok")]
    assert not not_ok, f"failed: {not_ok}\n{output}"
    stats = data.get("stats", {})
    assert stats.get("expected") == EXPECTED_TESTS, f"stats {stats}\n{output}"
    assert not stats.get("unexpected") and not stats.get("flaky") and not stats.get("skipped"), stats
    assert proc.returncode == 0, output
