"""T-P03-04 (log scrubbing), T-P03-05 (non-root images), T-P03-06 (security headers). Owner: P03.
All personal-data-shaped values below are synthetic test strings."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request

from _support import CADDY_IMAGE, CI, REPO, load, require_docker

pii = load("pii_scrub")


class ScrubTest(unittest.TestCase):
    def test_T_P03_04_scrubber_removes_nit_cedula_email_and_bank_account(self):
        raw = ("pago de cliente.sintetico@example.test NIT 900.123.456-7 y 800123456-1, "
               "cédula 1.020.304.050 / 52345678, cuenta 123-456789-01, cuenta2 001234567890123")
        out = pii.scrub(raw)
        for leaked in ("cliente.sintetico@example.test", "900.123.456-7", "800123456-1", "1.020.304.050",
                       "52345678", "123-456789-01", "001234567890123"):
            self.assertNotIn(leaked, out)
        for label in ("[EMAIL]", "[NIT]", "[DOC_ID]", "[CUENTA]"):
            self.assertIn(label, out)

    def test_T_P03_04_scrubber_keeps_ids_amounts_and_dates(self):
        keep = "tenant 0192f3a4-1234-7abc-8def-0123456789ab total 1500,00 COP el 2026-11-09 a las 10:15"
        self.assertEqual(pii.scrub(keep), keep)

    def test_T_P03_04_collector_config_is_generated_from_the_same_patterns(self):
        proc = subprocess.run([sys.executable, str(CI / "render_otel_scrub.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        config = (REPO / "deploy/observability/otel-collector.yaml").read_text(encoding="utf-8")
        patterns = json.loads((REPO / "deploy/observability/pii-patterns.json").read_text(encoding="utf-8"))
        for p in patterns["patterns"]:
            self.assertIn(p["replacement"], config)
        self.assertIn("transform/scrub_pii, batch]", config)

    def test_T_P03_04_patterns_are_re2_safe(self):
        patterns = json.loads((REPO / "deploy/observability/pii-patterns.json").read_text(encoding="utf-8"))
        for p in patterns["patterns"]:
            self.assertIsNone(re.search(r"\(\?[=!<]|\\[1-9]", p["regex"]), p["name"])  # no lookaround/backrefs


class ImagesTest(unittest.TestCase):
    def test_T_P03_05_dockerfiles_run_as_non_root_with_pinned_bases(self):
        for name in ("backend.Dockerfile", "frontend.Dockerfile"):
            text = (REPO / "deploy/docker" / name).read_text(encoding="utf-8")
            self.assertRegex(text, r"(?m)^USER 10001:10001$", name)
            for arg in re.findall(r"(?m)^ARG \w+_IMAGE=(\S+)$", text):
                self.assertIn("@sha256:", arg, name)
            self.assertNotIn(":latest", text)
            # the last USER instruction of the file is non-root
            users = re.findall(r"(?m)^USER (\S+)$", text)
            self.assertNotIn(users[-1].split(":")[0], ("root", "0"))

    def test_T_P03_05_built_application_images_and_proxy_are_non_root(self):
        require_docker()
        proc = subprocess.run(["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}"],
                              capture_output=True, text=True, check=True)
        app_images = [i for i in proc.stdout.split() if i.startswith("erp-")]
        for image in app_images:
            user = subprocess.run(["docker", "image", "inspect", image, "--format", "{{.Config.User}}"],
                                  capture_output=True, text=True, check=True).stdout.strip()
            self.assertNotIn(user.split(":")[0], ("", "root", "0"), image)
        uid = subprocess.run(["docker", "run", "--rm", "--user", "10001:10001", "--network", "none",
                              "--entrypoint", "id", CADDY_IMAGE, "-u"], capture_output=True, text=True, check=True)
        self.assertEqual(uid.stdout.strip(), "10001")


class HeadersTest(unittest.TestCase):
    def test_T_P03_06_proxy_sends_csp_hsts_nosniff_frame_ancestors(self):
        require_docker()
        name = "nexus-p03-headers-test"
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
        # published on a random LOOPBACK port only (policy D-002)
        subprocess.run(["docker", "run", "-d", "--name", name, "-p", "127.0.0.1::8080", "--user", "10001:10001",
                        "--read-only", "--tmpfs", "/tmp", "-e", "XDG_CONFIG_HOME=/tmp/c", "-e", "XDG_DATA_HOME=/tmp/d",
                        "-v", f"{REPO / 'deploy/dev/Caddyfile'}:/etc/caddy/Caddyfile:ro",
                        "-v", f"{REPO / 'deploy/dev/fallback'}:/srv/fallback:ro", CADDY_IMAGE,
                        "caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"],
                       capture_output=True, check=True)
        try:
            port = subprocess.run(["docker", "port", name, "8080/tcp"], capture_output=True, text=True,
                                  check=True).stdout.split()[0].rsplit(":", 1)[1]
            results = {}
            for path in ("/healthz", "/", "/api/v1/anything"):
                for _ in range(40):
                    status, headers = fetch(f"http://127.0.0.1:{port}{path}")
                    if status:
                        break
                    time.sleep(0.25)
                results[path] = (status, headers)
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
        for path, (status, headers) in results.items():
            self.assertIn("frame-ancestors 'none'", headers.get("content-security-policy", ""), path)
            self.assertIn("max-age=", headers.get("strict-transport-security", ""), path)
            self.assertEqual(headers.get("x-content-type-options"), "nosniff", path)
            self.assertNotIn("server", headers, path)
        self.assertEqual(results["/healthz"][0], 200)
        self.assertEqual(results["/"][0], 502)  # honest placeholder page: no frontend running
        self.assertEqual(results["/api/v1/anything"][0], 503)


def fetch(url: str) -> tuple[int, dict[str, str]]:
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:  # noqa: S310 - loopback test URL
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as exc:
        return exc.code, {k.lower(): v for k, v in exc.headers.items()}
    except OSError:
        return 0, {}


if __name__ == "__main__":
    unittest.main()
