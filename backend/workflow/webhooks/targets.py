"""Webhook target guard (SSRF): DEV-only builds deliver only to local/test hosts (owner P07)."""

from __future__ import annotations

from urllib.parse import urlsplit

from kernel.errors import ValidationFailed

DEV_HOST_SUFFIXES = (".localhost", ".test", ".invalid", ".local", ".example")
DEV_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal", "mailpit", "webhook-sink"}


def validate_url(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        raise ValidationFailed("webhook URL must be an absolute http(s) URL")
    if parts.username or parts.password:
        raise ValidationFailed("credentials in webhook URLs are not allowed")
    if host not in DEV_HOSTS and not host.endswith(DEV_HOST_SUFFIXES):
        raise ValidationFailed(
            "DEV-only build: webhook targets must be local/test hosts", code="production_endpoint_refused"
        )
    return url
