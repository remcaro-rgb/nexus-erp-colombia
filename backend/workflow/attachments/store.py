"""FileStore implementation for DEV (local directory standing in for MinIO/S3).

* Keys are tenant-prefixed: ``t/<tenant_id>/<uuid7>/<safe-name>``; a key of
  another tenant is reported as not found.
* Signed URLs: HMAC-SHA256 tokens bound to tenant + key + expiry (short TTL).
* Retention lock / legal hold: locked objects cannot be deleted before their
  date (the S3 equivalent is Object Lock in compliance mode).
* Server-side encryption is a property of the bucket (MinIO SSE) in DEV; this
  local store keeps files with mode 0600 under the configured root.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from kernel import clock, ids
from kernel.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from kernel.interfaces.files import StoredFile

SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
MAX_FILE_BYTES = 25 * 1024 * 1024
DEFAULT_TTL_SECONDS = 300
MAX_TTL_SECONDS = 3600


def safe_name(name: str) -> str:
    base = name.replace("\\", "/").split("/")[-1].strip()
    cleaned = SAFE_NAME.sub("_", base).strip("._")[:120]
    if not cleaned:
        raise ValidationFailed("invalid file name")
    return cleaned


@dataclass(frozen=True)
class SignedUrl:
    url: str
    token: str
    expires_at: dt.datetime


class LocalFileStore:
    def __init__(self, root: Path, signing_key: bytes) -> None:
        if not signing_key:
            raise ValidationFailed("file store signing key is empty")
        self.root = Path(root)
        self.signing_key = signing_key

    @classmethod
    def from_settings(cls) -> LocalFileStore:
        root = Path(getattr(settings, "ERP_FILESTORE_ROOT", "") or (settings.BASE_DIR.parent / ".filestore"))
        key = hashlib.sha256(("filestore:" + settings.SECRET_KEY).encode()).digest()
        return cls(root, key)

    # -- keys -------------------------------------------------------------------
    @staticmethod
    def _prefix(tenant_id: uuid.UUID) -> str:
        return f"t/{tenant_id}/"

    def _path(self, tenant_id: uuid.UUID, key: str) -> Path:
        if not key.startswith(self._prefix(tenant_id)) or ".." in key.split("/"):
            raise NotFound("file not found")
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents:
            raise NotFound("file not found")
        return path

    def _meta_path(self, path: Path) -> Path:
        return path.with_name(path.name + ".meta.json")

    # -- FileStore protocol -----------------------------------------------------
    def put(self, tenant_id: uuid.UUID, name: str, content: bytes, media_type: str) -> StoredFile:
        if len(content) > MAX_FILE_BYTES:
            raise ValidationFailed(f"file larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
        key = f"{self._prefix(tenant_id)}{ids.new_id()}/{safe_name(name)}"
        path = self._path(tenant_id, key)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_bytes(content)
        path.chmod(0o600)
        digest = hashlib.sha256(content).hexdigest()
        self._meta_path(path).write_text(json.dumps({"media_type": media_type, "sha256": digest}))
        return StoredFile(key, len(content), digest, media_type)

    def get(self, tenant_id: uuid.UUID, key: str) -> bytes:
        path = self._path(tenant_id, key)
        if not path.is_file():
            raise NotFound("file not found")
        return path.read_bytes()

    def delete(self, tenant_id: uuid.UUID, key: str) -> None:
        path = self._path(tenant_id, key)
        meta_path = self._meta_path(path)
        if meta_path.is_file():
            meta = json.loads(meta_path.read_text())
            until = meta.get("retain_until")
            if meta.get("legal_hold") or (until and dt.date.fromisoformat(until) >= clock.business_date()):
                raise Conflict("object is under retention lock or legal hold", code="retention_locked")
            meta_path.unlink()
        if path.is_file():
            path.unlink()

    # -- retention ----------------------------------------------------------------
    def _update_meta(self, tenant_id: uuid.UUID, key: str, **values: object) -> None:
        path = self._path(tenant_id, key)
        meta_path = self._meta_path(path)
        meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
        meta.update(values)
        meta_path.write_text(json.dumps(meta))

    def lock_retention(self, tenant_id: uuid.UUID, key: str, *, until: dt.date) -> None:
        self._update_meta(tenant_id, key, retain_until=until.isoformat())

    def set_legal_hold(self, tenant_id: uuid.UUID, key: str, hold: bool) -> None:
        self._update_meta(tenant_id, key, legal_hold=hold)

    # -- signed URLs ----------------------------------------------------------------
    def sign(
        self, tenant_id: uuid.UUID, key: str, *, ttl_seconds: int = DEFAULT_TTL_SECONDS, base_url: str = ""
    ) -> SignedUrl:
        if not 1 <= ttl_seconds <= MAX_TTL_SECONDS:
            raise ValidationFailed(f"ttl must be between 1 and {MAX_TTL_SECONDS} seconds")
        self._path(tenant_id, key)
        expires = clock.now() + dt.timedelta(seconds=ttl_seconds)
        payload = json.dumps({"t": str(tenant_id), "k": key, "e": int(expires.timestamp())}, separators=(",", ":"))
        body = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
        mac = hmac.new(self.signing_key, body.encode(), hashlib.sha256).hexdigest()
        token = f"{body}.{mac}"
        return SignedUrl(f"{base_url}?token={token}", token, expires)

    def resolve_token(self, tenant_id: uuid.UUID, token: str) -> str:
        """Key of a valid token for ``tenant_id``; ``PermissionDenied`` if forged, expired or foreign."""
        try:
            body, mac = token.rsplit(".", 1)
        except ValueError:
            raise PermissionDenied("invalid signed URL", code="signed_url_invalid") from None
        expected = hmac.new(self.signing_key, body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(mac, expected):
            raise PermissionDenied("invalid signed URL", code="signed_url_invalid")
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if payload["t"] != str(tenant_id):
            raise PermissionDenied("invalid signed URL", code="signed_url_invalid")
        if clock.now().timestamp() > payload["e"]:
            raise PermissionDenied("signed URL expired", code="signed_url_expired")
        return str(payload["k"])
