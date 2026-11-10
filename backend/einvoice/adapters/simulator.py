"""``SandboxSimulator`` — deterministic DIAN validation stand-in (FIXTURE_NOT_CERTIFIED, tests and DEV only).

The response is a pure function of the request: the fixture switch ``fixture_outcome`` (``accept`` |
``accept_with_notices`` | ``reject`` | ``transient``) and the attempt number (``transient`` fails on the
first attempt only). It checks what DIAN checks first — the XML parses, carries the declared UUID and a
signature — and refuses to run when ``FISCAL_MODE=production`` or for environment ``1``.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET

from einvoice import mode
from einvoice.adapters.base import AdapterConfig, NumberingRangeInfo, SubmitRequest, SubmitResult
from einvoice.errors import AdapterNotSupported, TransientTransmissionError
from kernel import io_guard

LABEL = "SIMULADO — NO CERTIFICADO"
DS = "http://www.w3.org/2000/09/xmldsig#"
CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"


def _response(request: SubmitRequest, status: str, messages: tuple[str, ...]) -> bytes:
    root = ET.Element("SimulatedApplicationResponse", {"simulated": "true"})
    ET.SubElement(root, "DocumentKey").text = request.unique_code
    ET.SubElement(root, "Status").text = status
    ET.SubElement(root, "Label").text = LABEL
    for m in messages:
        ET.SubElement(root, "Message").text = m
    return ET.tostring(root, encoding="UTF-8", xml_declaration=True)


class SandboxSimulator:
    key = "simulator"
    simulated = True
    certified = False

    def __init__(self, config: AdapterConfig | None = None) -> None:
        mode.refuse_simulator_in_production()
        self.config = config or AdapterConfig(adapter="simulator")

    def _tracking(self, request: SubmitRequest) -> str:
        return "SIM-" + hashlib.sha256(f"{request.kind}|{request.document_id}|{request.unique_code}".encode()).hexdigest()[:24]

    def _structural_errors(self, request: SubmitRequest) -> list[str]:
        try:
            root = ET.fromstring(request.xml)
        except ET.ParseError:
            return ["SIM-XML: documento mal formado"]
        errors = []
        uuid_el = root.find(f".//{{{CBC}}}UUID")
        if request.kind == "document":
            declared = uuid_el.text if uuid_el is not None else root.findtext(".//{*}InformacionGeneral") or None
            info = root.find(".//{*}InformacionGeneral")
            if info is not None:
                declared = info.get("CUNE")
            if declared != request.unique_code:
                errors.append("SIM-UUID: el código único del XML no coincide con el enviado")
        if root.find(f".//{{{DS}}}Signature") is None:
            errors.append("SIM-FIRMA: el documento no está firmado")
        return errors

    def submit(self, request: SubmitRequest) -> SubmitResult:
        io_guard.check("dian-simulator")
        mode.refuse_simulator_in_production()
        mode.require_transmission_allowed(request.environment)
        tracking = self._tracking(request)
        errors = self._structural_errors(request)
        outcome = request.fixture_outcome
        if errors:
            return SubmitResult("REJECTED", tracking, request.unique_code, True, (LABEL,), tuple(errors),
                                _response(request, "REJECTED", tuple(errors)))  # fmt: skip
        if outcome == "transient" and request.attempt <= 1:
            raise TransientTransmissionError("SIM-503: servicio no disponible (fallo inyectado)")
        if outcome == "reject":
            msgs = ("SIM-RECHAZO: rechazo determinista del simulador (fixture 'reject')",)
            return SubmitResult("REJECTED", tracking, request.unique_code, True, (LABEL, *msgs), ("SIM-REJ-01",),
                                _response(request, "REJECTED", msgs))  # fmt: skip
        if outcome == "accept_with_notices":
            msgs = ("SIM-NOTIF: aceptado con notificaciones (fixture)",)
            return SubmitResult("ACCEPTED_WITH_NOTICES", tracking, request.unique_code, True, (LABEL, *msgs), (),
                                _response(request, "ACCEPTED_WITH_NOTICES", msgs))  # fmt: skip
        return SubmitResult("ACCEPTED", tracking, request.unique_code, True, (LABEL,), (),
                            _response(request, "ACCEPTED", ()))  # fmt: skip

    def status(self, request: SubmitRequest, tracking_id: str) -> SubmitResult:
        io_guard.check("dian-simulator")
        if tracking_id != self._tracking(request):
            return SubmitResult("REJECTED", tracking_id, None, True, (LABEL,), ("SIM-NOTFOUND",))
        return self.submit(SubmitRequest(**{**_fields(request), "attempt": max(request.attempt, 2)}))

    def numbering_ranges(self, company_nit: str, software_id: str) -> list[NumberingRangeInfo]:
        io_guard.check("dian-simulator")
        raise AdapterNotSupported(
            "the simulator has no DIAN numbering ranges: load the resolution manually (synthetic in DEV)"
        )


def _fields(request: SubmitRequest) -> dict[str, object]:
    return {name: getattr(request, name) for name in request.__dataclass_fields__}
