"""Permission code matching (``<module>.<resource>.<action>``, fnmatch per code)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from fnmatch import fnmatchcase

CODE_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")

CLASSIFICATIONS = ("public", "internal", "confidential", "restricted-pii", "sensitive-pii", "secret")


def valid_code(code: str) -> bool:
    return bool(CODE_RE.match(code))


def matches(patterns: Iterable[str], code: str) -> bool:
    return any(fnmatchcase(code, p) for p in patterns)


def role_allows(grants: Iterable[str], denies: Iterable[str], code: str) -> bool:
    """Deny wins inside a role; no grant → deny."""
    return matches(grants, code) and not matches(denies, code)


def classification_rank(name: str) -> int:
    try:
        return CLASSIFICATIONS.index(name)
    except ValueError:
        return len(CLASSIFICATIONS)  # unknown = most sensitive (fail closed)
