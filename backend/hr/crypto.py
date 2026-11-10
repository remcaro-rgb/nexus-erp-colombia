"""Field encryption for the employee bank account (confidential; encrypted at rest).

Construction (stdlib only; no new dependency): encrypt-then-MAC with HMAC-SHA256 as the PRF —
keystream block ``i`` = HMAC(k_enc, nonce ‖ i) XOR plaintext (CTR mode over a PRF), tag =
HMAC(k_mac, version ‖ nonce ‖ ciphertext). ``k_enc`` / ``k_mac`` are derived from
``settings.ERP_FIELD_ENCRYPTION_KEY`` (url-safe base64 of 32 bytes, generated into the gitignored
dev secrets file). A missing key fails closed. CCR P13a-3 proposes a shared kernel helper.
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
        hmac.new(master, b"hr.field.enc", hashlib.sha256).digest(),
        hmac.new(master, b"hr.field.mac", hashlib.sha256).digest(),
    )


def _stream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:length])


def encrypt(plaintext: str | None) -> str | None:
    if plaintext is None:
        return None
    k_enc, k_mac = _keys()
    nonce = os.urandom(16)
    data = plaintext.encode()
    cipher = bytes(a ^ b for a, b in zip(data, _stream(k_enc, nonce, len(data)), strict=True))
    tag = hmac.new(k_mac, VERSION + nonce + cipher, hashlib.sha256).digest()
    return VERSION.decode() + ":" + base64.urlsafe_b64encode(nonce + tag + cipher).decode()


def decrypt(token: str | None) -> str | None:
    if token is None:
        return None
    k_enc, k_mac = _keys()
    version, _, body = token.partition(":")
    blob = base64.urlsafe_b64decode(body.encode())
    nonce, tag, cipher = blob[:16], blob[16:48], blob[48:]
    expected = hmac.new(k_mac, version.encode() + nonce + cipher, hashlib.sha256).digest()
    if version.encode() != VERSION or not hmac.compare_digest(tag, expected):
        raise ValueError("bank account ciphertext failed authentication")
    return bytes(a ^ b for a, b in zip(cipher, _stream(k_enc, nonce, len(cipher)), strict=True)).decode()
