"""T-P03-01 (compose policy + dev port selection, D-002/D-P03). Owner: P03."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _support import CI, REPO, load

lint = load("lint_compose")
select_port = load("select_port")


class ComposeLintTest(unittest.TestCase):
    def test_T_P03_01_repo_compose_files_pass_policy_lint(self):
        proc = subprocess.run([sys.executable, str(CI / "lint_compose.py")], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_T_P03_01_lint_rejects_ports_restart_digest_and_secret_violations(self):
        model = {
            "services": {
                "proxy": {"image": "caddy@sha256:" + "a" * 64, "restart": "unless-stopped",
                          "healthcheck": {"test": ["CMD", "true"]},
                          "ports": [{"published": "8080", "target": 8080, "host_ip": ""}]},
                "db": {"image": "postgres:18", "restart": "always",
                       "ports": [{"published": "5432", "target": 5432, "host_ip": "127.0.0.1"}]},
                "cache": {"image": "valkey/valkey:latest", "restart": "unless-stopped",
                          "healthcheck": {"test": ["CMD", "true"]}},
                "migrate": {"image": "erp-api:dev", "labels": {"nexus.oneshot": "true"}, "restart": "always"},
            },
            "networks": {"backend": {}},
        }
        errors: list[str] = []
        lint.check_model("t", model, errors, raw_ports=False)
        joined = "\n".join(errors)
        for needle in ("'db' publishes a port", "publishes on '0.0.0.0", "restart must be 'unless-stopped'",
                       "'db' has no healthcheck", "unpinned/latest", "not pinned by digest", "must not restart",
                       "network 'backend' must be internal"):
            self.assertIn(needle, joined)
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "compose.yaml"
            bad.write_text("services:\n  x:\n    environment:\n      ERP_DB_PASSWORD: test-literal-value\n"
                           "      ERP_OK_PASSWORD: ${ERP_OK_PASSWORD:?}\n    ports: ['0.0.0.0:80:80']\n")
            orig = lint.REPO
            lint.REPO = Path(tmp)
            try:
                errs: list[str] = []
                lint.check_raw_text(bad, errs)
            finally:
                lint.REPO = orig
        self.assertTrue(any("literal value for secret-like variable ERP_DB_PASSWORD" in e for e in errs), errs)
        self.assertFalse(any("ERP_OK_PASSWORD" in e for e in errs), errs)
        self.assertTrue(any("0.0.0.0" in e for e in errs), errs)

    def test_T_P03_01_dev_runtime_publishes_only_proxy_on_loopback_and_has_no_build(self):
        text = (REPO / "deploy/dev/compose.yaml").read_text(encoding="utf-8")
        self.assertEqual(text.count("ports:"), 1)
        self.assertIn('"${ERP_DEV_BIND_HOST:-127.0.0.1}:${ERP_DEV_PORT:-8080}:8080"', text)
        self.assertNotRegex(text, r"(?m)^\s+build\s*:")


class PortSelectionTest(unittest.TestCase):
    def test_T_P03_01_preferred_port_used_when_free(self):
        self.assertEqual(select_port.select(8080, set(), lambda p: True), (8080, []))

    def test_T_P03_01_falls_back_to_first_free_port_of_8081_8090(self):
        busy = {8080, 8081, 8082}
        port, seen_busy = select_port.select(8080, set(), lambda p: p not in busy)
        self.assertEqual(port, 8083)
        self.assertEqual(seen_busy, [8080, 8081, 8082])

    def test_T_P03_01_port_owned_by_our_stack_is_kept(self):
        self.assertEqual(select_port.select(8085, {8085}, lambda p: False)[0], 8085)

    def test_T_P03_01_all_busy_exits_3_listing_busy_ports(self):
        holders = []
        try:
            for port in [8080] + list(range(8081, 8091)):
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    s.bind(("127.0.0.1", port))
                    s.listen(1)
                    holders.append(s)
                except OSError:
                    s.close()  # already busy (e.g. our own dev stack): still counts as busy
            env = dict(os.environ, ERP_DEV_PORT="8080")
            proc = subprocess.run([sys.executable, str(CI / "select_port.py")], capture_output=True, text=True, env=env)
        finally:
            for s in holders:
                s.close()
        self.assertEqual(proc.returncode, 3, proc.stdout)
        self.assertIn("BLOCKED_DEV_RUNTIME", proc.stderr)
        for port in [8080] + list(range(8081, 8091)):
            self.assertIn(str(port), proc.stderr)

    def test_T_P03_01_real_bind_check_detects_a_busy_port(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        try:
            self.assertFalse(select_port.is_free(s.getsockname()[1]))
        finally:
            s.close()


if __name__ == "__main__":
    unittest.main()
