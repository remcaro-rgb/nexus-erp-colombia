"""Enveloped XML-DSig signature with a XAdES-EPES ``QualifyingProperties`` block (worker side only).

**DEV fixture (FIXTURE_NOT_CERTIFIED).** The DIAN signature policy needs an RSA certificate from an
accredited certification entity (EG-2, external) and a maintained XML-DSig library; neither ``signxml``,
``xmlsec`` nor ``cryptography`` is pinned in this repository (CCR ``contracts/change-requests/P15-1.md``).
Until then the signer produces the full DIAN structure (enveloped ``Reference URI=""``, a reference to
``xades:SignedProperties`` with signing time, certificate digest and the signature-policy identifier) but
the ``SignatureMethod`` is ``hmac-sha256`` keyed by a secret held only by the worker. The verifier
recomputes both digests and the signature value from the parsed document, so any tampering fails.
Canonicalization: W3C C14N 2.0 (stdlib) of each signed element serialized on its own.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from einvoice import annex
from einvoice.builders.ubl import NS
from einvoice.secrets import Secret
from kernel import clock

DS = NS["ds"]
XADES = NS["xades"]
EXT = NS["ext"]
C14N = "http://www.w3.org/2006/12/xml-c14n11"
ENVELOPED = "http://www.w3.org/2000/09/xmldsig#enveloped-signature"
SHA256 = "http://www.w3.org/2001/04/xmlenc#sha256"
HMAC_SHA256 = "http://www.w3.org/2001/04/xmldsig-more#hmac-sha256"
SIGNED_PROPS_TYPE = "http://uri.etsi.org/01903#SignedProperties"
#: DIAN signature policy v2 identifier (FE annex v1.8 §; re-verify for v1.9 — B-06).
POLICY_URL = "https://facturaelectronica.dian.gov.co/politicadefirma/v2/politicadefirmav2.pdf"
POLICY_STATUS = annex.OFFICIAL_PRIOR
SIGNATURE_MODE = "FIXTURE_NOT_CERTIFIED"


class SignatureInvalid(Exception):
    pass


@dataclass(frozen=True, slots=True)
class CertificateInfo:
    subject: str
    thumbprint: str  # hex SHA-256 of the certificate (metadata, never the key)
    serial: str = ""


def _c14n(elem: ET.Element) -> bytes:
    return ET.canonicalize(ET.tostring(elem, encoding="unicode"), strip_text=False).encode("utf-8")


def _b64_digest(data: bytes) -> str:
    return base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")


def _signature_slot(root: ET.Element) -> ET.Element:
    exts = root.find(f"{{{EXT}}}UBLExtensions")
    if exts is None:
        raise SignatureInvalid("document has no ext:UBLExtensions")
    contents = exts.findall(f"{{{EXT}}}UBLExtension/{{{EXT}}}ExtensionContent")
    if not contents:
        raise SignatureInvalid("document has no ExtensionContent for the signature")
    return contents[-1]


def _strip_signature(root: ET.Element) -> ET.Element | None:
    slot = _signature_slot(root)
    sig = slot.find(f"{{{DS}}}Signature")
    if sig is not None:
        slot.remove(sig)
    return sig


def _sub(parent: ET.Element, ns: str, tag: str, text: str | None = None, **attrs: str) -> ET.Element:
    el = ET.SubElement(parent, f"{{{ns}}}{tag}", attrs)
    if text is not None:
        el.text = text
    return el


def sign(xml: bytes, key: Secret, cert: CertificateInfo, *, signature_id: str) -> bytes:
    """Return ``xml`` with ``ds:Signature`` inserted in the last ``ext:ExtensionContent``."""
    root = ET.fromstring(xml)
    if _strip_signature(root) is not None:
        raise SignatureInvalid("document is already signed")
    doc_digest = _b64_digest(_c14n(root))

    sig = ET.Element(f"{{{DS}}}Signature", {"Id": f"xmldsig-{signature_id}"})
    signed_info = _sub(sig, DS, "SignedInfo")
    _sub(signed_info, DS, "CanonicalizationMethod", Algorithm=C14N)
    _sub(signed_info, DS, "SignatureMethod", Algorithm=HMAC_SHA256)
    ref = _sub(signed_info, DS, "Reference", Id=f"xmldsig-{signature_id}-ref0", URI="")
    _sub(_sub(ref, DS, "Transforms"), DS, "Transform", Algorithm=ENVELOPED)
    _sub(ref, DS, "DigestMethod", Algorithm=SHA256)
    _sub(ref, DS, "DigestValue", doc_digest)

    obj = ET.Element(f"{{{DS}}}Object")
    qp = _sub(obj, XADES, "QualifyingProperties", Target=f"#xmldsig-{signature_id}")
    sp = _sub(qp, XADES, "SignedProperties", Id=f"xmldsig-{signature_id}-signedprops")
    ssp = _sub(sp, XADES, "SignedSignatureProperties")
    _sub(ssp, XADES, "SigningTime", clock.now().isoformat(timespec="seconds"))
    cert_el = _sub(_sub(ssp, XADES, "SigningCertificate"), XADES, "Cert")
    digest = _sub(cert_el, XADES, "CertDigest")
    _sub(digest, DS, "DigestMethod", Algorithm=SHA256)
    _sub(digest, DS, "DigestValue", base64.b64encode(bytes.fromhex(cert.thumbprint)).decode("ascii"))
    issuer = _sub(cert_el, XADES, "IssuerSerial")
    _sub(issuer, DS, "X509IssuerName", cert.subject)
    _sub(issuer, DS, "X509SerialNumber", cert.serial or "0")
    policy = _sub(_sub(_sub(ssp, XADES, "SignaturePolicyIdentifier"), XADES, "SignaturePolicyId"), XADES, "SigPolicyId")
    _sub(policy, XADES, "Identifier", POLICY_URL)
    _sub(policy, XADES, "Description", f"Política de firma DIAN v2 ({SIGNATURE_MODE}, {POLICY_STATUS})")
    _sub(_sub(_sub(ssp, XADES, "SignerRole"), XADES, "ClaimedRoles"), XADES, "ClaimedRole", "supplier")

    ref_props = _sub(signed_info, DS, "Reference", Type=SIGNED_PROPS_TYPE, URI=f"#xmldsig-{signature_id}-signedprops")
    _sub(ref_props, DS, "DigestMethod", Algorithm=SHA256)
    _sub(ref_props, DS, "DigestValue", _b64_digest(_c14n(sp)))

    value = hmac.new(key.reveal().encode("utf-8"), _c14n(signed_info), hashlib.sha256).digest()
    _sub(sig, DS, "SignatureValue", base64.b64encode(value).decode("ascii"), Id=f"xmldsig-{signature_id}-sigvalue")
    key_info = _sub(sig, DS, "KeyInfo")
    _sub(key_info, DS, "KeyName", f"{SIGNATURE_MODE}:{cert.thumbprint[:16]}")
    sig.append(obj)
    _signature_slot(root).append(sig)
    return ET.tostring(root, encoding="UTF-8", xml_declaration=True)


def verify(xml: bytes, key: Secret) -> dict[str, str]:
    """Verify a document signed by :func:`sign`; returns the signed properties. Raises ``SignatureInvalid``."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise SignatureInvalid(f"not well-formed XML: {exc}") from None
    sig = _strip_signature(root)
    if sig is None:
        raise SignatureInvalid("no ds:Signature")
    signed_info = sig.find(f"{{{DS}}}SignedInfo")
    sig_value = sig.find(f"{{{DS}}}SignatureValue")
    if signed_info is None or sig_value is None or not sig_value.text:
        raise SignatureInvalid("incomplete signature")
    method = signed_info.find(f"{{{DS}}}SignatureMethod")
    if method is None or method.get("Algorithm") != HMAC_SHA256:
        raise SignatureInvalid("unsupported signature method")
    refs = signed_info.findall(f"{{{DS}}}Reference")
    if len(refs) != 2:
        raise SignatureInvalid("expected the document and SignedProperties references")
    doc_ref, props_ref = refs
    if doc_ref.get("URI") != "" or doc_ref.findtext(f"{{{DS}}}DigestValue") != _b64_digest(_c14n(root)):
        raise SignatureInvalid("document digest mismatch (content changed after signing)")
    sp_id = (props_ref.get("URI") or "").lstrip("#")
    sp = next((e for e in sig.iter(f"{{{XADES}}}SignedProperties") if e.get("Id") == sp_id), None)
    if sp is None or props_ref.findtext(f"{{{DS}}}DigestValue") != _b64_digest(_c14n(sp)):
        raise SignatureInvalid("SignedProperties digest mismatch")
    expected = hmac.new(key.reveal().encode("utf-8"), _c14n(signed_info), hashlib.sha256).digest()
    if not hmac.compare_digest(base64.b64encode(expected).decode("ascii"), sig_value.text.strip()):
        raise SignatureInvalid("signature value mismatch")
    return {
        "signing_time": sp.findtext(f".//{{{XADES}}}SigningTime") or "",
        "policy": sp.findtext(f".//{{{XADES}}}Identifier") or "",
        "role": sp.findtext(f".//{{{XADES}}}ClaimedRole") or "",
    }
