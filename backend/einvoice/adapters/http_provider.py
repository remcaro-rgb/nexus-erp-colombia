"""Generic HTTP connector for a technology provider (PT), configurable per PT with mapping hooks (ADR-007).

* **Transport** — :class:`UrllibTransport` (stdlib; ``kernel.io_guard`` refuses it inside a unit of work).
  Tests and the habilitación helper inject another transport (e.g. an in-memory PT sandbox).
* **Mapping** — how a :class:`SubmitRequest` becomes an HTTP request and how the PT answer becomes a
  :class:`SubmitResult`. ``generic-json-v1`` is the reference mapping; a PT with another API registers its
  own :class:`Mapping` under its ``contract_version`` with :func:`register_mapping` (no other code changes).
* **Safety** — only ``environment == SANDBOX`` providers, only ``https`` (``http`` only for loopback test
  doubles), credentials sent only in the ``Authorization`` header, every error text redacted.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from einvoice import mode
from einvoice.adapters.base import AdapterConfig, NumberingRangeInfo, SubmitRequest, SubmitResult
from einvoice.errors import AdapterNotSupported, ProviderNotConfigured, TransientTransmissionError
from einvoice.secrets import redact
from kernel import io_guard

#: Hostnames that are DIAN production services: never called by this edition.
PRODUCTION_HOSTS = frozenset({"vpfe.dian.gov.co", "catalogo-vpfe.dian.gov.co"})
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1", "pt-sandbox.invalid"})


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    body: bytes
    headers: dict[str, str]


class Transport(Protocol):
    def send(self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> HttpResponse: ...


class UrllibTransport:
    def send(self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> HttpResponse:
        io_guard.check(urllib.parse.urlsplit(url).hostname or "provider")
        req = urllib.request.Request(url, data=body, method=method, headers=headers)  # noqa: S310 - scheme checked by the adapter
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                return HttpResponse(resp.status, resp.read(), dict(resp.headers.items()))
        except urllib.error.HTTPError as exc:
            return HttpResponse(exc.code, exc.read() or b"", dict(exc.headers.items()) if exc.headers else {})
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TransientTransmissionError(redact(f"provider unreachable: {exc}")) from None


class Mapping(Protocol):
    version: str

    def submit_request(self, config: AdapterConfig, request: SubmitRequest) -> tuple[str, str, dict[str, str], bytes]:
        """(method, path, headers, body) — the adapter adds the Authorization header."""
        ...

    def status_request(self, config: AdapterConfig, request: SubmitRequest, tracking_id: str) -> tuple[str, str, dict[str, str], bytes | None]: ...

    def parse_result(self, request: SubmitRequest, response: HttpResponse) -> SubmitResult: ...

    def numbering_request(self, config: AdapterConfig, company_nit: str, software_id: str) -> tuple[str, str] | None: ...

    def parse_numbering(self, response: HttpResponse) -> list[NumberingRangeInfo]: ...


class GenericJsonMapping:
    """Reference PT mapping ``generic-json-v1`` (JSON over HTTPS)."""

    version = "generic-json-v1"

    def submit_request(self, config: AdapterConfig, request: SubmitRequest) -> tuple[str, str, dict[str, str], bytes]:
        body = {
            "document_id": str(request.document_id),
            "kind": request.kind,
            "family": request.family,
            "number": request.number,
            "unique_code": request.unique_code,
            "environment": request.environment,
            "test_set_id": request.test_set_id,
            "xml_base64": base64.b64encode(request.xml).decode("ascii"),
        }
        headers = {
            "Content-Type": "application/json",
            "Idempotency-Key": request.idempotency_key,
            "X-Fixture-Outcome": request.fixture_outcome,
            "X-Attempt": str(request.attempt),
        }
        return "POST", "/documents", headers, json.dumps(body, sort_keys=True).encode("utf-8")

    def status_request(self, config: AdapterConfig, request: SubmitRequest, tracking_id: str) -> tuple[str, str, dict[str, str], bytes | None]:
        return "GET", f"/documents/{urllib.parse.quote(tracking_id)}", {"Accept": "application/json"}, None

    def parse_result(self, request: SubmitRequest, response: HttpResponse) -> SubmitResult:
        if response.status == 429 or response.status >= 500:
            raise TransientTransmissionError(f"provider HTTP {response.status}")
        try:
            data = json.loads(response.body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise TransientTransmissionError("provider returned a non-JSON body") from None
        if response.status >= 400:
            codes = tuple(str(c) for c in data.get("errors") or [f"HTTP-{response.status}"])
            return SubmitResult("REJECTED", str(data.get("tracking_id", "")), None, bool(data.get("simulated", False)),
                                tuple(redact(m) for m in data.get("messages") or ()), codes)  # fmt: skip
        status = str(data.get("status", ""))
        if status not in ("ACCEPTED", "ACCEPTED_WITH_NOTICES", "REJECTED", "RETRYABLE"):
            raise TransientTransmissionError(f"provider returned an unknown status {status[:40]!r}")
        if status == "RETRYABLE":
            raise TransientTransmissionError("provider asked to retry")
        response_xml = base64.b64decode(data["application_response_base64"]) if data.get("application_response_base64") else b""
        return SubmitResult(
            status,  # type: ignore[arg-type]  # validated against the literal set above
            str(data.get("tracking_id", "")),
            data.get("unique_code"),
            bool(data.get("simulated", False)),
            tuple(redact(m) for m in data.get("messages") or ()),
            tuple(str(c) for c in data.get("errors") or ()),
            response_xml,
        )

    def numbering_request(self, config: AdapterConfig, company_nit: str, software_id: str) -> tuple[str, str] | None:
        query = urllib.parse.urlencode({"nit": company_nit, "software_id": software_id})
        return "GET", f"/numbering-ranges?{query}"

    def parse_numbering(self, response: HttpResponse) -> list[NumberingRangeInfo]:
        import datetime as dt

        from einvoice.secrets import Secret, register_sensitive

        if response.status >= 400:
            raise TransientTransmissionError(f"provider HTTP {response.status}")
        out = []
        for row in json.loads(response.body.decode("utf-8") or "[]"):
            key = str(row.get("technical_key") or "")
            register_sensitive(key)
            out.append(
                NumberingRangeInfo(str(row["prefix"]), int(row["from"]), int(row["to"]), str(row["resolution"]),
                                   dt.date.fromisoformat(row["valid_from"]), dt.date.fromisoformat(row["valid_to"]),
                                   Secret("technical_key", key) if key else None)  # fmt: skip
            )
        return out


_mappings: dict[str, Callable[[], Mapping]] = {"generic-json-v1": GenericJsonMapping}


def register_mapping(version: str, factory: Callable[[], Mapping]) -> None:
    _mappings[version] = factory


def mapping_for(version: str) -> Mapping:
    factory = _mappings.get(version)
    if factory is None:
        raise ProviderNotConfigured(f"no mapping registered for contract version {version!r}")
    return factory()


class HttpProviderAdapter:
    key = "http_generic"
    simulated = False
    certified = False

    def __init__(self, config: AdapterConfig, transport: Transport | None = None, timeout: float = 20.0) -> None:
        if config.environment != "SANDBOX":
            raise ProviderNotConfigured("only SANDBOX technology-provider environments exist in this edition")
        parts = urllib.parse.urlsplit(config.base_url)
        host = (parts.hostname or "").lower()
        if host in PRODUCTION_HOSTS:
            raise ProviderNotConfigured("production DIAN endpoints are refused")
        if parts.scheme != "https" and not (parts.scheme == "http" and host in LOOPBACK):
            raise ProviderNotConfigured("provider base_url must be https (http only for loopback test doubles)")
        self.config = config
        self.mapping = mapping_for(config.mapping_version)
        self.transport: Transport = transport or config.options.get("transport") or UrllibTransport()
        self.timeout = timeout
        self.simulated = bool(config.options.get("simulated", False))

    def _headers(self, extra: dict[str, str]) -> dict[str, str]:
        headers = dict(extra)
        if self.config.credentials is not None:
            headers["Authorization"] = f"Bearer {self.config.credentials.reveal()}"
        return headers

    def _send(self, method: str, path: str, headers: dict[str, str], body: bytes | None) -> HttpResponse:
        io_guard.check(urllib.parse.urlsplit(self.config.base_url).hostname or "provider")
        url = self.config.base_url.rstrip("/") + path
        try:
            return self.transport.send(method, url, self._headers(headers), body, self.timeout)
        except TransientTransmissionError as exc:
            raise TransientTransmissionError(redact(str(exc))) from None
        except (OSError, ValueError) as exc:
            raise TransientTransmissionError(redact(f"transport error: {exc}")) from None

    def submit(self, request: SubmitRequest) -> SubmitResult:
        mode.require_transmission_allowed(request.environment)
        method, path, headers, body = self.mapping.submit_request(self.config, request)
        started = time.monotonic()
        result = self.mapping.parse_result(request, self._send(method, path, headers, body))
        return _with_latency(result, started)

    def status(self, request: SubmitRequest, tracking_id: str) -> SubmitResult:
        method, path, headers, body = self.mapping.status_request(self.config, request, tracking_id)
        return self.mapping.parse_result(request, self._send(method, path, headers, body))

    def numbering_ranges(self, company_nit: str, software_id: str) -> list[NumberingRangeInfo]:
        spec = self.mapping.numbering_request(self.config, company_nit, software_id)
        if spec is None:
            raise AdapterNotSupported("this provider mapping has no numbering-range query")
        return self.mapping.parse_numbering(self._send(spec[0], spec[1], {"Accept": "application/json"}, None))


def _with_latency(result: SubmitResult, started: float) -> SubmitResult:
    from dataclasses import replace

    return replace(result, latency_ms=int((time.monotonic() - started) * 1000))


def describe(config: AdapterConfig) -> dict[str, Any]:
    """Masked description (never the credentials)."""
    return {"adapter": config.adapter, "base_url": config.base_url, "environment": config.environment,
            "mapping_version": config.mapping_version, "credentials": "***" if config.credentials else None}  # fmt: skip
