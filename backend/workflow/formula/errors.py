"""Stable error codes of the formula engine (contracts/errors.yaml, module workflow).

Every error is a :class:`kernel.errors.DomainError` (HTTP 422) carrying the
position (0-based offset, 1-based line/column) when it refers to the source.
"""

from __future__ import annotations

from typing import Any

from kernel.errors import DomainError


class FormulaError(DomainError):
    code = "formula_error"
    status = 422
    title = "Formula error"

    def __init__(
        self, detail: str = "", *, position: int | None = None, source: str | None = None, **extra: Any
    ) -> None:
        if position is not None:
            extra["position"] = position
            if source is not None:
                line = source.count("\n", 0, position) + 1
                col = position - (source.rfind("\n", 0, position) + 1) + 1
                extra["line"] = line
                extra["column"] = col
        super().__init__(detail, **extra)
        self.position = position


class FormulaSyntaxError(FormulaError):
    code, title = "formula_syntax_error", "Formula syntax error"


class FormulaUnknownIdentifier(FormulaError):
    code, title = "formula_unknown_identifier", "Unknown identifier"


class FormulaTypeError(FormulaError):
    code, title = "formula_type_error", "Type error"


class FormulaBudgetExceeded(FormulaError):
    code, title = "formula_budget_exceeded", "Evaluation budget exceeded (D-008)"


class FormulaDependencyCycle(FormulaError):
    code, title = "formula_dependency_cycle", "Dependency cycle"


class FormulaTestCaseFailed(FormulaError):
    code, title = "formula_test_case_failed", "Formula test case failed"


class FormulaDivisionByZero(FormulaError):
    code, title = "formula_division_by_zero", "Division by zero"


class FormulaNotApproved(FormulaError):
    code, title = "formula_not_approved", "Formula version not approved"


FORMULA_ERROR_CODES = tuple(
    cls.code
    for cls in (
        FormulaSyntaxError,
        FormulaUnknownIdentifier,
        FormulaTypeError,
        FormulaBudgetExceeded,
        FormulaDependencyCycle,
        FormulaTestCaseFailed,
        FormulaDivisionByZero,
        FormulaNotApproved,
    )
)
