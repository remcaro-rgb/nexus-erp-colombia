"""Outbound HTTP adapter of the webhooks (ADR-004: HTTP clients live only in adapter modules).

:class:`HttpxTransport` re-checks every target with :func:`workflow.webhooks.targets.validate_url`
(SSRF guard, DEV-only hosts) right before sending; redirects are never followed.
"""

from __future__ import annotations

from collections.abc import Mapping

from workflow.webhooks.targets import validate_url

TIMEOUT_SECONDS = 5.0


class HttpxTransport:
    def send(self, url: str, headers: Mapping[str, str], body: bytes) -> int:
        import httpx  # dev dependency group: imported only when a delivery is actually sent

        validate_url(url)
        resp = httpx.post(url, content=body, headers=dict(headers), timeout=TIMEOUT_SECONDS, follow_redirects=False)
        return int(resp.status_code)
