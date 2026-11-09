"""Transports for the leak suite.

* :class:`DjangoClientTransport` - in-process ``django.test.Client`` against the real URLconf.
  Authentication is pluggable: ``authenticator(principal) -> headers``. P04 provides the real one
  (OIDC/JWT for users, RFC 8693 delegated tokens for AI, OAuth scopes for MCP); until then the
  transport reports authenticated cases as BLOCKED rather than inventing credentials.
* :class:`HttpTransport` - the same over HTTP (``http.client``, loopback only) for the dev stack.

Both refuse any non-loopback base URL: the leak suite never talks to anything but the local build.
"""

from __future__ import annotations

import http.client
import json
import urllib.parse
from collections.abc import Callable
from typing import Any

from .leak import Response
from .openapi import Operation
from .principals import Principal

Authenticator = Callable[[Principal], dict[str, str]]

LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1", "testserver"})


def _decode(content: bytes, content_type: str) -> Any:
    if "json" in content_type:
        try:
            return json.loads(content.decode("utf-8") or "null")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return content.decode("utf-8", errors="replace")
    return content.decode("utf-8", errors="replace")


class DjangoClientTransport:
    name = "django-client"

    def __init__(self, authenticator: Authenticator | None = None) -> None:
        from django.test import Client

        self.client = Client(raise_request_exception=False)
        self.authenticator = authenticator

    def implemented(self, op: Operation) -> bool:
        from .leak import concrete_path
        from .routes import resolves

        return resolves(concrete_path(op, None))

    def can_authenticate(self, principal: Principal) -> bool:
        return self.authenticator is not None

    def request(self, op: Operation, path: str, principal: Principal | None, headers: dict[str, str]) -> Response:
        hdrs = dict(headers)
        if principal is not None:
            if self.authenticator is None:
                raise RuntimeError("no authenticator configured")
            hdrs.update(self.authenticator(principal))
        kwargs: dict[str, Any] = {"headers": hdrs}
        if op.method in ("post", "put", "patch"):
            kwargs["data"] = "{}"
            kwargs["content_type"] = "application/json"
        resp = getattr(self.client, op.method)(path, **kwargs)
        ctype = resp.get("Content-Type", "")
        body = _decode(b"".join(resp.streaming_content) if resp.streaming else resp.content, ctype)
        return Response(status=resp.status_code, body=body, headers=dict(resp.items()))


class HttpTransport:
    name = "http"

    def __init__(self, base_url: str, authenticator: Authenticator | None = None, timeout: int = 30) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in ("http", "https") or parsed.hostname not in LOOPBACK:
            raise ValueError(f"refusing non-loopback target {base_url!r} (DEV-only, D-002)")
        self.parsed = parsed
        self.authenticator = authenticator
        self.timeout = timeout

    def implemented(self, op: Operation) -> bool:
        # Over HTTP a missing route is indistinguishable from BOLA's 404: callers pass the list of
        # implemented operations (e.g. from the served OpenAPI) by subclassing; default = all.
        return True

    def can_authenticate(self, principal: Principal) -> bool:
        return self.authenticator is not None

    def raw(self, method: str, path: str, headers: dict[str, str], body: bytes | None = None) -> Response:
        conn_cls = http.client.HTTPSConnection if self.parsed.scheme == "https" else http.client.HTTPConnection
        conn = conn_cls(self.parsed.hostname or "127.0.0.1", self.parsed.port, timeout=self.timeout)
        try:
            conn.request(method.upper(), path, body=body, headers=headers)
            r = conn.getresponse()
            content = r.read()
            hdrs = {k: v for k, v in r.getheaders()}
            return Response(status=r.status, body=_decode(content, r.getheader("Content-Type", "")), headers=hdrs)
        finally:
            conn.close()

    def request(self, op: Operation, path: str, principal: Principal | None, headers: dict[str, str]) -> Response:
        hdrs = dict(headers)
        if principal is not None:
            if self.authenticator is None:
                raise RuntimeError("no authenticator configured")
            hdrs.update(self.authenticator(principal))
        body = None
        if op.method in ("post", "put", "patch"):
            body = b"{}"
            hdrs["Content-Type"] = "application/json"
        return self.raw(op.method, path, hdrs, body)
