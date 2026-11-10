"""Ledger error codes (contracts/errors.yaml, module ledger)."""

from __future__ import annotations

from typing import Any

from kernel.errors import Conflict, DomainError, ValidationFailed


class LedgerError(DomainError):
    """422 business error with a stable ledger code."""

    code, status, title = "ledger_error", 422, "Ledger rule violated"


class DimensionInUse(Conflict):
    code, title = "dimension_in_use", "Node in use"


def ledger_error(code: str, detail: str, **extra: Any) -> LedgerError:
    return LedgerError(detail, code=code, **extra)


def validation(detail: str, pointer: str, reason: str) -> ValidationFailed:
    return ValidationFailed(detail, pointer=pointer, reason=reason)


CODES = (
    "entry_unbalanced",
    "account_not_postable",
    "account_determination_missing",
    "fx_rate_missing",
    "period_closed",
    "period_locked",
    "dimension_node_not_postable",
    "dimension_required",
    "dimension_forbidden",
    "dimension_cross_company",
    "dimension_cycle",
    "dimension_depth_exceeded",
    "dimension_in_use",
    "dimension_archived",
    "dimension_code_duplicate",
)
