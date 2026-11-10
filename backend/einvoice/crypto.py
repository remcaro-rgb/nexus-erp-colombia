"""Evidence encryption at rest (request/response XML, ADR-012) — DEV envelope.

Encrypt-then-MAC with stdlib primitives only (no AES library is pinned; CCR P15-1 asks for
``cryptography`` to switch to AES-256-GCM): a per-record random 16-byte nonce, a keystream of
``HMAC-SHA256(k_enc, nonce ‖ counter)`` blocks XOR-ed with the plaintext, and a
``HMAC-SHA256(k_mac, version ‖ nonce ‖ ciphertext)`` tag. ``k_enc``/``k_mac`` are derived from the
field-encryption key (``settings.ERP_FIELD_ENCRYPTION_KEY``, generated into the gitignored secrets
file) with distinct labels. Tampering or a wrong key raises :class:`EvidenceIntegrityError`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

from django.conf import settings

VERSION = b"\x01"
BLOCK = 32


class EvidenceIntegrityError(Exception):
    pass


def _master() -> bytes:
    raw = getattr(settings, "ERP_FIELD_ENCRYPTION_KEY", None)
    if not raw:
        raise EvidenceIntegrityError("ERP_FIELD_ENCRYPTION_KEY is not configured")
    key = raw if isinstance(raw, bytes) else str(raw).encode("ascii")
    try:
        decoded = base64.urlsafe_b64decode(key)
    except (ValueError, TypeError):
        decoded = key
    return hashlib.sha256(b"einvoice-evidence-v1|" + decoded).digest()


def _keys() -> tuple[bytes, bytes]:
    master = _master()
    return (
        hmac.new(master, b"enc", hashlib.sha256).digest(),
        hmac.new(master, b"mac", hashlib.sha256).digest(),
    )


def _stream(k_enc: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hmac.new(k_enc, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:length])


def encrypt(plaintext: bytes) -> bytes:
    k_enc, k_mac = _keys()
    nonce = os.urandom(16)
    ct = bytes(a ^ b for a, b in zip(plaintext, _stream(k_enc, nonce, len(plaintext)), strict=True))
    tag = hmac.new(k_mac, VERSION + nonce + ct, hashlib.sha256).digest()
    return VERSION + nonce + tag + ct


def decrypt(blob: bytes) -> bytes:
    if len(blob) < 1 + 16 + 32 or blob[:1] != VERSION:
        raise EvidenceIntegrityError("unknown evidence envelope")
    k_enc, k_mac = _keys()
    nonce, tag, ct = blob[1:17], blob[17:49], blob[49:]
    if not hmac.compare_digest(tag, hmac.new(k_mac, VERSION + nonce + ct, hashlib.sha256).digest()):
        raise EvidenceIntegrityError("evidence integrity check failed")
    return bytes(a ^ b for a, b in zip(ct, _stream(k_enc, nonce, len(ct)), strict=True))
