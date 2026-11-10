"""Test doubles that prove pluggability (T-PT-OPEN-01).

* :class:`LedgerDummyAdapter` — a *second*, independent ``ProviderAdapter`` implementation (not derived from
  the simulator): it keeps its own in-memory "PT ledger" keyed by the CUFE, answers from the fixture
  switch and requires a credential, like a real PT would.
* :class:`InMemoryPtSandbox` — a fake PT HTTP API (``generic-json-v1`` mapping) used as the transport of
  the real :class:`~einvoice.adapters.http_provider.HttpProviderAdapter`; it records every request so the
  tests can check headers (credentials only in ``Authorization``) and bodies.

Both are synthetic test fixtures (FIXTURE_NOT_CERTIFIED); neither opens a socket.
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from einvoice import mode
from einvoice.adapters.base import AdapterConfig, NumberingRangeInfo, SubmitRequest, SubmitResult
from einvoice.adapters.http_provider import HttpResponse
from einvoice.errors import AdapterNotSupported, ProviderNotConfigured, TransientTransmissionError
from kernel import io_guard

LABEL = "DUMMY PT — FIXTURE_NOT_CERTIFIED"


class LedgerDummyAdapter:
    key = "dummy-pt"
    simulated = True
    certified = False
    #: shared across instances like a remote PT would be (cleared by the tests)
    ledger: dict[str, SubmitResult] = {}
    calls: list[str] = []

    def __init__(self, config: AdapterConfig) -> None:
        if config.credentials is None:
            raise ProviderNotConfigured("dummy PT needs credentials")
        self._token = config.credentials

    def submit(self, request: SubmitRequest) -> SubmitResult:
        io_guard.check("dummy-pt")
        if request.environment != "2" or mode.is_production():
            raise ProviderNotConfigured("dummy PT only accepts habilitación/pruebas (environment 2)")
        type(self).calls.append(request.unique_code)
        key = f"{request.kind}:{request.unique_code}:{request.fixture_outcome}"
        if request.fixture_outcome == "transient" and request.attempt <= 1:
            raise TransientTransmissionError("dummy PT: 503 temporarily unavailable")
        if key in self.ledger:
            return self.ledger[key]
        tracking = "DUMMY-" + hashlib.sha1(key.encode(), usedforsecurity=False).hexdigest()[:20]
        outcome = {"accept": "ACCEPTED", "accept_with_notices": "ACCEPTED_WITH_NOTICES", "reject": "REJECTED"}.get(
            request.fixture_outcome, "ACCEPTED"
        )
        messages: tuple[str, ...] = (LABEL,)
        if outcome == "ACCEPTED_WITH_NOTICES":
            messages += ("DUMMY-N01: notificación de prueba",)
        codes = ("DUMMY-R01",) if outcome == "REJECTED" else ()
        result = SubmitResult(outcome, tracking, request.unique_code, True, messages, codes, b"<DummyAck/>")  # type: ignore[arg-type]
        self.ledger[key] = result
        return result

    def status(self, request: SubmitRequest, tracking_id: str) -> SubmitResult:
        io_guard.check("dummy-pt")
        for result in self.ledger.values():
            if result.tracking_id == tracking_id:
                return result
        return SubmitResult("REJECTED", tracking_id, None, True, (LABEL,), ("DUMMY-404",))

    def numbering_ranges(self, company_nit: str, software_id: str) -> list[NumberingRangeInfo]:
        io_guard.check("dummy-pt")
        raise AdapterNotSupported("dummy PT has no numbering-range query")


@dataclass
class InMemoryPtSandbox:
    """Fake PT (``generic-json-v1``): POST /documents, GET /documents/{tracking}, GET /numbering-ranges."""

    expected_token: str
    requests: list[dict[str, Any]] = field(default_factory=list)
    store: dict[str, dict[str, Any]] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    ranges: list[dict[str, Any]] = field(default_factory=list)

    def send(self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> HttpResponse:
        with self.lock:
            self.requests.append({"method": method, "url": url, "headers": dict(headers), "body": body or b""})
        if headers.get("Authorization") != f"Bearer {self.expected_token}":
            return HttpResponse(401, json.dumps({"errors": ["PT-AUTH"], "messages": ["unauthorized"]}).encode(), {})
        path = urllib.parse.urlsplit(url).path
        if method == "GET" and path.endswith("/numbering-ranges"):
            return HttpResponse(200, json.dumps(self.ranges).encode(), {})
        if method == "GET" and "/documents/" in path:
            tracking = urllib.parse.unquote(path.rsplit("/documents/", 1)[1])
            for row in self.store.values():
                if row["tracking_id"] == tracking:
                    return HttpResponse(200, json.dumps(row).encode(), {})
            return HttpResponse(404, json.dumps({"errors": ["PT-404"]}).encode(), {})
        data = json.loads((body or b"{}").decode())
        outcome = headers.get("X-Fixture-Outcome", "accept")
        if outcome == "transient" and headers.get("X-Attempt") == "1":
            return HttpResponse(503, b"{}", {})
        key = f"{data['kind']}:{data['unique_code']}:{outcome}"
        if key not in self.store:
            xml = base64.b64decode(data["xml_base64"])
            status = {"accept": "ACCEPTED", "accept_with_notices": "ACCEPTED_WITH_NOTICES", "reject": "REJECTED"}.get(
                outcome, "ACCEPTED"
            )
            if b"Signature" not in xml:
                status = "REJECTED"
            self.store[key] = {
                "status": status,
                "tracking_id": "PT-" + hashlib.sha256(key.encode()).hexdigest()[:20],
                "unique_code": data["unique_code"],
                "simulated": True,
                "messages": ["PT SANDBOX — sintético"]
                + (["PT-N01 aviso"] if status == "ACCEPTED_WITH_NOTICES" else []),
                "errors": ["PT-R01"] if status == "REJECTED" else [],
                "application_response_base64": base64.b64encode(b"<PtApplicationResponse/>").decode(),
            }
        return HttpResponse(200, json.dumps(self.store[key]).encode(), {})
