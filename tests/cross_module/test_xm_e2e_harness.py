"""P22a: static guarantees of the Playwright harness (runs without node or a browser)."""

from __future__ import annotations

import re

from qa_harness.paths import ROOT

E2E = ROOT / "tests" / "e2e"


def test_p22a_playwright_config_is_dev_safe_and_localized():
    cfg = (E2E / "playwright.config.ts").read_text(encoding="utf-8")
    for needle in (
        "process.env.CHROME_PATH",
        "locale: 'es-CO'",
        "timezoneId: 'America/Bogota'",
        "reducedMotion: 'reduce'",
        "trace: 'retain-on-failure'",
        "forbidOnly: true",
        "refusing non-loopback target",
        "reports/playwright/results.json",
    ):
        assert needle in cfg, needle
    assert "0.0.0.0" not in cfg  # noqa: S104 - asserting its absence
    assert not re.search(r"https?://(?!127\.0\.0\.1|localhost)[a-z]", cfg), "no external URLs"


def test_p22a_e2e_support_loads_nothing_from_cdns():
    for path in E2E.rglob("*"):
        if path.suffix in (".ts", ".mjs", ".js"):
            text = path.read_text(encoding="utf-8")
            assert not re.search(r"https?://(cdn|unpkg|jsdelivr|cdnjs)", text), path
            assert "test.only(" not in text and "test.skip(" not in text, path


def test_p22a_dev_banner_text_matches_d_p08():
    fixtures = (E2E / "support" / "fixtures.ts").read_text(encoding="utf-8")
    assert "AMBIENTE DE DESARROLLO — DATOS SINTÉTICOS — NO VÁLIDO PARA EFECTOS LEGALES O TRIBUTARIOS" in fixtures
