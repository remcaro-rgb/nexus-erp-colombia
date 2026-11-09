"""Minimal RS256 JWT (RFC 7515/7518/7519) for OIDC access-token verification.

The lock file (owned by P01) has no JOSE library, so this module implements the
one algorithm the realm uses: RSASSA-PKCS1-v1_5 with SHA-256. Verification
rebuilds the full expected EMSA-PKCS1-v1_5 encoding and compares it in constant
time (no padding parsing, so no Bleichenbacher-style parser bugs). Only ``RS256``
is accepted; ``alg=none``/HS* tokens are rejected before any key is used.
Key generation exists for the local dev/test issuer only (CCR P04-1 asks P01 to
add PyJWT + cryptography; the call sites only use :func:`decode`/:func:`encode`).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from typing import Any

# DigestInfo prefix for SHA-256 (RFC 8017 §9.2 note 1)
_SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")
_MIN_BITS = 2048


class JWTError(Exception):
    """Malformed, unsigned, wrongly signed or otherwise invalid token."""


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    if not isinstance(text, str) or any(c not in _B64URL for c in text):
        raise JWTError("invalid base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


_B64URL = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def _int_to_b64(value: int) -> str:
    return b64url_encode(value.to_bytes((value.bit_length() + 7) // 8, "big"))


def _b64_to_int(text: str) -> int:
    return int.from_bytes(b64url_decode(text), "big")


@dataclass(frozen=True, slots=True)
class RSAPublicKey:
    n: int
    e: int
    kid: str

    @property
    def size_bytes(self) -> int:
        return (self.n.bit_length() + 7) // 8

    def to_jwk(self) -> dict[str, str]:
        return {
            "kty": "RSA",
            "alg": "RS256",
            "use": "sig",
            "kid": self.kid,
            "n": _int_to_b64(self.n),
            "e": _int_to_b64(self.e),
        }

    @classmethod
    def from_jwk(cls, jwk: dict[str, Any]) -> RSAPublicKey:
        if jwk.get("kty") != "RSA" or jwk.get("alg", "RS256") != "RS256" or jwk.get("use", "sig") != "sig":
            raise JWTError("unsupported JWK")
        key = cls(_b64_to_int(jwk["n"]), _b64_to_int(jwk["e"]), str(jwk.get("kid", "")))
        if key.n.bit_length() < _MIN_BITS or key.e < 3 or key.e % 2 == 0:
            raise JWTError("weak RSA key")
        return key


@dataclass(frozen=True, slots=True)
class RSAPrivateKey:
    n: int
    e: int
    d: int
    kid: str

    def public(self) -> RSAPublicKey:
        return RSAPublicKey(self.n, self.e, self.kid)


def _emsa_pkcs1_v15(message: bytes, size: int) -> bytes:
    t = _SHA256_PREFIX + hashlib.sha256(message).digest()
    if size < len(t) + 11:
        raise JWTError("key too small")
    return b"\x00\x01" + b"\xff" * (size - len(t) - 3) + b"\x00" + t


def _is_probable_prime(n: int, rounds: int = 40) -> bool:
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _random_prime(bits: int) -> int:
    while True:
        candidate = secrets.randbits(bits) | (1 << (bits - 1)) | (1 << (bits - 2)) | 1
        if _is_probable_prime(candidate):
            return candidate


def generate_key(bits: int = _MIN_BITS, kid: str | None = None) -> RSAPrivateKey:
    """RSA key for the local dev/test issuer only (Keycloak signs in the dev stack)."""
    e = 65537
    while True:
        p, q = _random_prime(bits // 2), _random_prime(bits // 2)
        if p == q:
            continue
        n = p * q
        phi = (p - 1) * (q - 1)
        if n.bit_length() != bits or phi % e == 0:
            continue
        d = pow(e, -1, phi)
        return RSAPrivateKey(n, e, d, kid or secrets.token_hex(8))


def encode(claims: dict[str, Any], key: RSAPrivateKey, headers: dict[str, Any] | None = None) -> str:
    header = {"alg": "RS256", "typ": "JWT", "kid": key.kid, **(headers or {})}
    signing_input = (
        b64url_encode(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + b64url_encode(json.dumps(claims, separators=(",", ":"), default=str).encode())
    )
    size = (key.n.bit_length() + 7) // 8
    em = int.from_bytes(_emsa_pkcs1_v15(signing_input.encode("ascii"), size), "big")
    signature = pow(em, key.d, key.n).to_bytes(size, "big")
    return signing_input + "." + b64url_encode(signature)


def unverified_header(token: str) -> dict[str, Any]:
    parts = token.split(".") if isinstance(token, str) else []
    if len(parts) != 3:
        raise JWTError("malformed token")
    try:
        header = json.loads(b64url_decode(parts[0]))
    except (ValueError, UnicodeDecodeError) as exc:
        raise JWTError("malformed header") from exc
    if not isinstance(header, dict):
        raise JWTError("malformed header")
    return header


def decode(token: str, keys: dict[str, RSAPublicKey]) -> dict[str, Any]:
    """Verify the RS256 signature with the key named by ``kid``; return the claims.

    Claim validation (iss/aud/exp/nbf/azp) is the caller's job (:mod:`iam.oidc.verifier`).
    """
    header = unverified_header(token)
    if header.get("alg") != "RS256":
        raise JWTError("unsupported alg")
    if "crit" in header:
        raise JWTError("unsupported critical header")
    key = keys.get(str(header.get("kid", "")))
    if key is None:
        raise JWTError("unknown signing key")
    head_b64, body_b64, sig_b64 = token.split(".")
    signature = b64url_decode(sig_b64)
    if len(signature) != key.size_bytes:
        raise JWTError("bad signature length")
    s = int.from_bytes(signature, "big")
    if s >= key.n:
        raise JWTError("bad signature")
    em = pow(s, key.e, key.n).to_bytes(key.size_bytes, "big")
    expected = _emsa_pkcs1_v15(f"{head_b64}.{body_b64}".encode("ascii"), key.size_bytes)
    if not hmac.compare_digest(em, expected):
        raise JWTError("bad signature")
    try:
        claims = json.loads(b64url_decode(body_b64))
    except (ValueError, UnicodeDecodeError) as exc:
        raise JWTError("malformed claims") from exc
    if not isinstance(claims, dict):
        raise JWTError("malformed claims")
    return claims
