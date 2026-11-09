#!/usr/bin/env python3
"""Compose policy lint (D-P03 CI rule, D-002, D-028). Owner: P03.

Fails (exit 1) on:
  * a published port on any service other than `proxy`, or bound to a non-loopback host / `0.0.0.0` default
  * a long-running service whose restart policy is not `unless-stopped`, or without a healthcheck
    (one-shot jobs are labelled `nexus.oneshot: "true"`)
  * a third-party image without an @sha256 digest, any `:latest` tag, an app image not named erp-<service>:${ERP_IMAGE_TAG}
  * a `build:` section in the DEV runtime file deploy/dev/compose.yaml
  * a literal secret (a *PASSWORD/SECRET/TOKEN/KEY variable whose value is not an interpolation)
  * the `backend` network not being internal
Usage: lint_compose.py [compose files...]  (default: every compose file of the repo, each with its overlays)
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXAMPLE_ENV = REPO / "deploy" / "dev" / "dev.env.example"
DEV_RUNTIME = REPO / "deploy" / "dev" / "compose.yaml"
STACKS = [
    [DEV_RUNTIME],
    [REPO / "docker-compose.yml"],
    [REPO / "docker-compose.yml", REPO / "docker-compose.dev.yml"],
    [REPO / "docker-compose.yml", REPO / "docker-compose.test.yml"],
    [REPO / "docker-compose.yml", REPO / "docker-compose.prod.yml"],
]
SECRET_KEY_RE = re.compile(r"(PASSWORD|SECRET|TOKEN|_KEY|PASSWD|CREDENTIAL)", re.I)
NOT_SECRET_KEYS = re.compile(r"(_USER|_USERNAME|_FILE|ACCESS_KEY|SETTINGS_MODULE)$", re.I)
ENV_LINE_RE = re.compile(r"^\s*-?\s*([A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*(.+?)\s*$")
LOOPBACK = {"127.0.0.1", "::1"}
APP_IMAGE_RE = re.compile(r"^erp-[a-z-]+:")


def rel(path: Path) -> str:
    return str(path.relative_to(REPO))


def resolved_config(files: list[Path]) -> dict:
    """Fully resolved model (extends, anchors, overlays, all profiles) via `docker compose config`."""
    cmd = ["docker", "compose", "--project-directory", str(files[0].parent)]
    for f in files:
        cmd += ["-f", str(f)]
    cmd += ["--env-file", str(EXAMPLE_ENV), "--profile", "*", "config", "--format", "json"]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False,
                          env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(Path.home())})
    if proc.returncode != 0:
        raise RuntimeError(f"docker compose config failed for {[rel(f) for f in files]}: {proc.stderr.strip()[:400]}")
    return json.loads(proc.stdout)


def yaml_config(files: list[Path]) -> dict:
    """Fallback without docker: raw YAML of a single file (anchors/merge keys resolved by PyYAML)."""
    import yaml  # type: ignore[import-untyped]

    return yaml.safe_load(files[-1].read_text(encoding="utf-8"))


def check_model(label: str, model: dict, errors: list[str], raw_ports: bool) -> None:
    for name, svc in (model.get("services") or {}).items():
        labels = svc.get("labels") or {}
        if isinstance(labels, list):
            labels = dict(item.split("=", 1) for item in labels)
        oneshot = str(labels.get("nexus.oneshot", "")).lower() == "true"
        for port in svc.get("ports") or []:
            if name != "proxy":
                errors.append(f"{label}: service '{name}' publishes a port ({port}); only 'proxy' may")
            if isinstance(port, dict):
                host = port.get("host_ip") or ""
                if port.get("published") and host not in LOOPBACK:
                    errors.append(f"{label}: service '{name}' publishes on '{host or '0.0.0.0 (all interfaces)'}'")
            elif raw_ports:
                text = str(port)
                if not (text.startswith("${ERP_DEV_BIND_HOST:-127.0.0.1}:") or text.startswith("127.0.0.1:")):
                    errors.append(f"{label}: service '{name}' port '{text}' is not bound to loopback by default")
        restart = svc.get("restart")
        if oneshot:
            if restart not in (None, "no"):
                errors.append(f"{label}: one-shot job '{name}' must not restart (got {restart})")
        else:
            if restart != "unless-stopped":
                errors.append(f"{label}: service '{name}' restart must be 'unless-stopped' (got {restart})")
            hc = svc.get("healthcheck")
            if not hc or hc.get("disable"):
                errors.append(f"{label}: service '{name}' has no healthcheck")
        image = str(svc.get("image") or "")
        if image.endswith(":latest") or (":" not in image.rsplit("/", 1)[-1] and "@" not in image and image):
            errors.append(f"{label}: service '{name}' uses an unpinned/latest image '{image}'")
        if image and not APP_IMAGE_RE.match(image) and "@sha256:" not in image:
            errors.append(f"{label}: third-party image of '{name}' is not pinned by digest ('{image}')")
    nets = model.get("networks") or {}
    if "backend" in nets and not (nets["backend"] or {}).get("internal"):
        errors.append(f"{label}: network 'backend' must be internal")


def check_raw_text(path: Path, errors: list[str]) -> None:
    text = path.read_text(encoding="utf-8")
    if "0.0.0.0" in text:
        errors.append(f"{rel(path)}: contains '0.0.0.0'")
    if path == DEV_RUNTIME and re.search(r"^\s+build\s*:", text, re.M):
        errors.append(f"{rel(path)}: the DEV runtime must not contain build: (images are built by scripts/ci/build-images.sh)")
    for no, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        m = ENV_LINE_RE.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip().strip('"').strip("'")
        if not SECRET_KEY_RE.search(key) or NOT_SECRET_KEYS.search(key):
            continue
        if value.startswith(("/", "{", "[")) or value in ("", "null"):
            continue
        if "${" not in value:
            errors.append(f"{rel(path)}:{no}: literal value for secret-like variable {key}")


def main(argv: list[str]) -> int:
    errors: list[str] = []
    use_docker = shutil.which("docker") is not None and subprocess.run(
        ["docker", "compose", "version"], capture_output=True, check=False).returncode == 0
    stacks = [[Path(a).resolve()] for a in argv[1:]] or STACKS
    seen: set[Path] = set()
    for files in stacks:
        label = " + ".join(rel(f) for f in files)
        try:
            model = resolved_config(files) if use_docker else yaml_config(files)
        except (RuntimeError, ValueError) as exc:
            errors.append(str(exc))
            continue
        check_model(label, model, errors, raw_ports=not use_docker)
        for f in files:
            if f not in seen:
                seen.add(f)
                check_raw_text(f, errors)
    if errors:
        sys.stderr.write("compose lint FAILED:\n" + "\n".join(f"  - {e}" for e in errors) + "\n")
        return 1
    sys.stdout.write(f"compose lint OK ({len(stacks)} stacks, {len(seen)} files, "
                     f"{'docker compose config' if use_docker else 'yaml'} model)\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
