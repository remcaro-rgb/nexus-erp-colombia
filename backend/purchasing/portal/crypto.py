"""Field encryption of supplier bank accounts (confidential; encrypted at rest, never logged or returned in clear).

Same stdlib construction as ``hr.crypto`` (encrypt-then-MAC: HMAC-SHA256 CTR keystream + HMAC tag), with
purchasing-specific derived keys from ``settings.ERP_FIELD_ENCRYPTION_KEY`` (generated into the gitignored dev
secrets). A missing key fails closed. CCR P13a-3 proposes one shared kernel helper; this module goes away then.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

from django.conf import settings

from kernel.errors import ServiceUnavailable

VERSION = b"v1"


def _keys() -> tuple[bytes, bytes]:
    raw = getattr(settings, "ERP_FIELD_ENCRYPTION_KEY", None)
    if not raw:
        raise ServiceUnavailable("field encryption key is not configured")
    master = base64.urlsafe_b64decode(str(raw).encode())
    return (
        hmac.new(master, b"purchasing.supplier.field.enc", hashlib.sha256).digest(),
        hmac.new(master, b"purchasing.supplier.field.mac", hashlib.sha256).digest(),
    )


def _stream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:length])


def encrypt(plaintext: str) -> str:
    k_enc, k_mac = _keys()
    nonce = os.urandom(16)
    data = plaintext.encode()
    cipher = bytes(a ^ b for a, b in zip(data, _stream(k_enc, nonce, len(data)), strict=True))
    tag = hmac.new(k_mac, VERSION + nonce + cipher, hashlib.sha256).digest()
    return VERSION.decode() + ":" + base64.urlsafe_b64encode(nonce + tag + cipher).decode()


def decrypt(token: str) -> str:
    k_enc, k_mac = _keys()
    version, _, body = token.partition(":")
    blob = base64.urlsafe_b64decode(body.encode())
    nonce, tag, cipher = blob[:16], blob[16:48], blob[48:]
    expected = hmac.new(k_mac, version.encode() + nonce + cipher, hashlib.sha256).digest()
    if version.encode() != VERSION or not hmac.compare_digest(tag, expected):
        raise ValueError("bank account ciphertext failed authentication")
    return bytes(a ^ b for a, b in zip(cipher, _stream(k_enc, nonce, len(cipher)), strict=True)).decode()


def masked(last4: str) -> str | None:
    return f"****{last4}" if last4 else None
