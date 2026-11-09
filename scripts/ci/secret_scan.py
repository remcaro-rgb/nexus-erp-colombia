#!/usr/bin/env python3
"""Lightweight secret scan over tracked files (stdlib only; gitleaks runs in addition in GitHub CI). Owner: P03.

Fails when a tracked file
  * lives in deploy/dev/secrets/ (other than README.md / .gitkeep), or is a *.env / .env.* file (except *.env.example)
  * contains a private key block, a cloud/API token shape, or a literal password assignment
Never prints the matched value — only file:line and the rule name.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RULES = [
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("password-literal", re.compile(
        r"""(?i)\b[\w.-]*(?:password|passwd|secret|api_key|apikey|token)[\w.-]*\s*[:=]\s*["']([^"'\s${}<>]{12,})["']""")),
    ("url-credentials", re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@${}]+:([^@\s${}]{8,})@")),
]
PLACEHOLDER = re.compile(r"(?i)example|placeholder|changeme|change_me|not-a-secret|dummy|redacted|\*\*\*|xxxx")
SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".woff", ".woff2", ".ttf", ".lock"}
ALLOWED_IN_SECRETS_DIR = {"deploy/dev/secrets/README.md", "deploy/dev/secrets/.gitkeep"}


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z"], capture_output=True, check=True)
    return [p for p in out.stdout.decode().split("\0") if p]


def scan(paths: list[str]) -> list[str]:
    hits: list[str] = []
    for rel in paths:
        name = rel.rsplit("/", 1)[-1]
        if rel.startswith("deploy/dev/secrets/") and rel not in ALLOWED_IN_SECRETS_DIR:
            hits.append(f"{rel}: tracked file inside deploy/dev/secrets/")
        if (name.endswith(".env") or name.startswith(".env.") or name == ".env") and not name.endswith(".example"):
            hits.append(f"{rel}: tracked env file")
        path = REPO / rel
        if path.suffix in SKIP_SUFFIX or not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for no, line in enumerate(text.splitlines(), 1):
            for rule, rx in RULES:
                m = rx.search(line)
                if not m:
                    continue
                value = m.group(1) if m.groups() else m.group(0)
                if PLACEHOLDER.search(value) or PLACEHOLDER.search(line) and rule in ("password-literal", "url-credentials"):
                    continue
                hits.append(f"{rel}:{no}: {rule}")
    return hits


def main(argv: list[str]) -> int:
    paths = argv[1:] or tracked_files()
    hits = scan(paths)
    if hits:
        sys.stderr.write("secret scan FAILED (values not shown):\n" + "\n".join(f"  - {h}" for h in hits) + "\n")
        return 1
    sys.stdout.write(f"secret scan OK ({len(paths)} files)\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
