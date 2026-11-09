"""Load a validated format pack into an immutable :class:`FormatPack` (F-ACC-EXO)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tax.exogena.schema import SYNTHETIC_LABEL, require_valid_pack

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SYNTHETIC_PACK_CODE = "SYN-EXO-1001"


@dataclass(frozen=True, slots=True)
class PackField:
    name: str
    type: str
    length: int
    required: bool
    enum: tuple[str, ...] = ()
    rules: tuple[str, ...] = ()
    decimals: int = 0


@dataclass(frozen=True, slots=True)
class PackRule:
    id: str
    kind: str
    severity: str
    fields: tuple[str, ...] = ()
    pattern: str = ""
    message_es: str = ""


@dataclass(frozen=True, slots=True)
class FormatPack:
    pack_code: str
    format_code: str
    year: int
    version: str
    source: str
    legal_status: str
    fields: tuple[PackField, ...]
    rules: tuple[PackRule, ...]
    file_spec: dict[str, Any]
    concepts: tuple[dict[str, Any], ...]
    checksum: str
    raw: dict[str, Any]

    @property
    def synthetic(self) -> bool:
        return self.source == "SYNTHETIC"

    @property
    def verified(self) -> bool:
        return self.legal_status == "VERIFIED_BY_USER"

    @property
    def label(self) -> str:
        if self.synthetic:
            return SYNTHETIC_LABEL
        return "Verificado por el usuario" if self.verified else "Pendiente de verificación"

    def field(self, name: str) -> PackField:
        for f in self.fields:
            if f.name == name:
                return f
        raise KeyError(name)


def canonical_json(pack: dict[str, Any]) -> bytes:
    return json.dumps(pack, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def checksum(pack: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(pack)).hexdigest()


def load_pack(pack: dict[str, Any]) -> FormatPack:
    """Validate (raises ``exogena_pack_invalid``) and build the immutable pack."""
    require_valid_pack(pack)
    fields = tuple(
        PackField(
            name=f["name"],
            type=f["type"],
            length=int(f["length"]),
            required=bool(f["required"]),
            enum=tuple(str(x) for x in f.get("enum", []) or []),
            rules=tuple(f.get("rules", []) or []),
            decimals=int(f.get("decimals", 0)),
        )
        for f in pack["fields"]
    )
    rules = tuple(
        PackRule(
            id=r["id"],
            kind=r["kind"],
            severity=r["severity"],
            fields=tuple(r.get("fields", []) or []),
            pattern=str(r.get("pattern", "")),
            message_es=str(r.get("message_es", "")),
        )
        for r in pack.get("rules", []) or []
    )
    return FormatPack(
        pack_code=pack["pack_code"],
        format_code=pack["format_code"],
        year=int(pack["year"]),
        version=pack["version"],
        source=pack["source"],
        legal_status=pack["legal_status"],
        fields=fields,
        rules=rules,
        file_spec=dict(pack["file_spec"]),
        concepts=tuple(pack.get("concepts", []) or []),
        checksum=checksum(pack),
        raw=pack,
    )


def read_pack_file(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def synthetic_pack_data() -> dict[str, Any]:
    return read_pack_file(FIXTURES / f"{SYNTHETIC_PACK_CODE}.json")


def load_synthetic_pack() -> FormatPack:
    return load_pack(synthetic_pack_data())
