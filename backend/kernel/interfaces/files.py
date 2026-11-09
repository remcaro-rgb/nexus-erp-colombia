"""FileStore (owner P07 attachments; MinIO in DEV).

Contract: object keys are namespaced by tenant; reads are policy-checked by
the caller's module; ``put`` outside a UoW or via on-commit hooks (it is I/O).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class StoredFile:
    key: str
    size: int
    sha256: str
    media_type: str


@runtime_checkable
class FileStore(Protocol):
    def put(self, tenant_id: uuid.UUID, name: str, content: bytes, media_type: str) -> StoredFile: ...

    def get(self, tenant_id: uuid.UUID, key: str) -> bytes:
        """Raises ``NotFound`` for a missing key or a key of another tenant."""
        ...

    def delete(self, tenant_id: uuid.UUID, key: str) -> None: ...
