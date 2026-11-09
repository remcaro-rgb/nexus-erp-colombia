#!/usr/bin/env python3
"""Health report for the DEV stack (used by dev-health.sh and deploy/dev/check-health.sh). Owner: P03.

Inputs via environment: NEXUS_PS_JSON (output of `docker compose ps --all --format json`), NEXUS_BASE
(proxy base URL on 127.0.0.1), NEXUS_PROFILES (active compose profiles), NEXUS_SECRETS_DIR.
Prints one JSON object; never prints secret values. Exit 0 iff every required check passed.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import urllib.error
import urllib.request
from pathlib import Path

INFRA = ["proxy", "db", "valkey", "keycloak", "minio", "mailpit"]
APP = ["api", "worker", "scheduler", "outbox-relay"]
REQUIRED_HEADERS = {
    "content-security-policy": "frame-ancestors",
    "strict-transport-security": "max-age=",
    "x-content-type-options": "nosniff",
}
REPO = Path(__file__).resolve().parents[2]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D401 - keep checks on-host
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def fetch(url: str, timeout: float = 5.0) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": "nexus-dev-health"})
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read(65536)
    except urllib.error.HTTPError as exc:
        return exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read(65536)
    except (urllib.error.URLError, OSError):
        return 0, {}, b""


def parse_ps(raw: str) -> dict[str, dict]:
    raw = raw.strip()
    if not raw:
        return {}
    if raw.startswith("["):
        rows = json.loads(raw)
    else:  # one JSON object per line (compose >= 2.21)
        rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    return {row.get("Service", ""): row for row in rows}


def service_checks(ps: dict[str, dict], names: list[str]) -> dict[str, str]:
    out = {}
    for name in names:
        row = ps.get(name)
        if row is None:
            out[name] = "MISSING"
        elif row.get("State") != "running":
            out[name] = f"NOT_RUNNING({row.get('State')})"
        elif row.get("Health") not in ("healthy", ""):
            out[name] = f"UNHEALTHY({row.get('Health')})"
        else:
            out[name] = "PASSED"
    return out


def main() -> int:
    base = os.environ.get("NEXUS_BASE", "http://127.0.0.1:8080").rstrip("/")
    profiles = {p for p in os.environ.get("NEXUS_PROFILES", "").split(",") if p}
    ps = parse_ps(os.environ.get("NEXUS_PS_JSON", ""))
    report: dict = {"base_url": base.replace("127.0.0.1", "localhost"), "profiles": sorted(profiles)}
    checks: dict[str, str] = {}

    apps = [a for a in os.environ.get("NEXUS_APP_SERVICES", "").split(",") if a]
    wanted = INFRA + apps
    report["not_available_yet"] = [a for a in APP + ["mcp", "web"] if a not in apps]
    report["services"] = service_checks(ps, wanted)

    # proxy + security headers (T-P03-06)
    status, headers, _ = fetch(f"{base}/healthz")
    checks["proxy_healthz"] = "PASSED" if status == 200 else f"FAILED(http {status})"
    missing = [h for h, needle in REQUIRED_HEADERS.items() if needle not in headers.get(h, "")]
    checks["security_headers"] = "PASSED" if status and not missing else f"FAILED(missing {','.join(missing)})"
    # ports: only 127.0.0.1 published, only by the proxy
    exposed = []
    for name, row in ps.items():
        for pub in row.get("Publishers") or []:
            if pub.get("PublishedPort"):
                exposed.append((name, pub.get("URL", "")))
    bad = [f"{n}@{u}" for n, u in exposed if n != "proxy" or u not in ("127.0.0.1", "::1")]
    checks["loopback_only"] = "PASSED" if not bad else f"FAILED({' '.join(bad)})"

    # Keycloak through the proxy: the erp-dev realm when P04's template exists, else the server itself.
    realm_file = REPO / "deploy" / "keycloak" / "realm-template.json"
    kc_path = "/auth/realms/erp-dev/.well-known/openid-configuration" if realm_file.exists() else "/auth/realms/master"
    status, _, _ = fetch(f"{base}{kc_path}", timeout=10)
    checks["keycloak"] = "PASSED" if status == 200 else f"FAILED(http {status} {kc_path})"

    if "app" in profiles:
        for path in ("/api/health/live", "/api/health/ready"):
            status, _, _ = fetch(f"{base}{path}")
            checks[path] = "PASSED" if status == 200 else f"FAILED(http {status})"
        status, _, body = fetch(f"{base}/api/v1/system/environment")
        try:
            env = json.loads(body or b"{}")
        except ValueError:
            env = {}
        ok = status == 200 and env.get("environment") == "dev"
        checks["/api/v1/system/environment"] = "PASSED" if ok else f"FAILED(http {status})"
        if ok:
            report["integrations"] = env.get("integrations", {})
    else:
        report["note"] = "application images not present yet: API checks not applicable"

    secrets_dir = Path(os.environ.get("NEXUS_SECRETS_DIR", REPO / "deploy" / "dev" / "secrets"))
    modes = []
    for name in ("dev.env", "dev-credentials.env"):
        f = secrets_dir / name
        if not f.exists() or stat.S_IMODE(f.stat().st_mode) != 0o600:
            modes.append(name)
    checks["secrets_mode_0600"] = "PASSED" if not modes else f"FAILED({','.join(modes)})"

    report["checks"] = checks
    ok = all(v == "PASSED" for v in checks.values()) and all(v == "PASSED" for v in report["services"].values())
    report["ok"] = ok
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
