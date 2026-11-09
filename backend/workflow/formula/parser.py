"""Recursive-descent parser for the formula DSL (hand-written, no ``eval``).

Grammar (lowest precedence first)::

    expr    := or
    or      := and ("or" and)*
    and     := not ("and" not)*
    not     := "not" not | cmp
    cmp     := add (("=="|"!="|"<"|"<="|">"|">=") add)?      # no chaining
    add     := mul (("+"|"-") mul)*
    mul     := unary (("*"|"/"|"%") unary)*
    unary   := ("-"|"+") unary | primary
    primary := NUMBER | STRING | "true" | "false" | NAME | NAME "(" [expr ("," expr)*] ")" | "(" expr ")"

Nesting depth and node count are enforced while parsing (D-008), so hostile
input never reaches Python's recursion limit.
"""

from __future__ import annotations

from decimal import Decimal

from workflow.formula.errors import FormulaBudgetExceeded, FormulaSyntaxError, FormulaUnknownIdentifier
from workflow.formula.functions import FUNCTIONS
from workflow.formula.lexer import Token, tokenize
from workflow.formula.limits import DEFAULT_LIMITS, Limits
from workflow.formula.nodes import Binary, Bool, Call, Ident, Node, Num, Str, Unary

CMP_OPS = frozenset({"==", "!=", "<", "<=", ">", ">="})
MAX_CALL_ARGS = 16


class _Parser:
    def __init__(self, source: str, limits: Limits) -> None:
        self.source = source
        self.limits = limits
        self.tokens = tokenize(source, limits)
        self.i = 0
        self.depth = 0
        self.nodes = 0

    # -- helpers -------------------------------------------------------------
    @property
    def tok(self) -> Token:
        return self.tokens[self.i]

    def advance(self) -> Token:
        t = self.tokens[self.i]
        self.i += 1
        return t

    def error(self, msg: str, at: int | None = None) -> FormulaSyntaxError:
        return FormulaSyntaxError(msg, position=self.tok.pos if at is None else at, source=self.source)

    def is_op(self, *ops: str) -> bool:
        return self.tok.kind == "OP" and self.tok.text in ops

    def is_kw(self, kw: str) -> bool:
        return self.tok.kind == "KW" and self.tok.text == kw

    def expect_op(self, op: str) -> Token:
        if not self.is_op(op):
            found = self.tok.text or "end of expression"
            raise self.error(f"expected '{op}' but found '{found}'")
        return self.advance()

    def count(self, node: Node) -> Node:
        self.nodes += 1
        if self.nodes > self.limits.max_ast_nodes:
            raise FormulaBudgetExceeded(f"more than {self.limits.max_ast_nodes} AST nodes", budget="ast_nodes")
        return node

    def enter(self) -> None:
        self.depth += 1
        if self.depth > self.limits.max_nesting:
            raise FormulaBudgetExceeded(
                f"nesting deeper than {self.limits.max_nesting}", budget="nesting", position=self.tok.pos
            )

    def leave(self) -> None:
        self.depth -= 1

    # -- grammar -------------------------------------------------------------
    def parse(self) -> Node:
        if self.tok.kind == "EOF":
            raise self.error("empty expression")
        node = self.parse_or()
        if self.tok.kind != "EOF":
            raise self.error(f"unexpected '{self.tok.text}'")
        return node

    def parse_or(self) -> Node:
        left = self.parse_and()
        while self.is_kw("or"):
            self.advance()
            right = self.parse_and()
            left = self.count(Binary(left.pos, right.end, "or", left, right))
        return left

    def parse_and(self) -> Node:
        left = self.parse_not()
        while self.is_kw("and"):
            self.advance()
            right = self.parse_not()
            left = self.count(Binary(left.pos, right.end, "and", left, right))
        return left

    def parse_not(self) -> Node:
        if self.is_kw("not"):
            t = self.advance()
            self.enter()
            operand = self.parse_not()
            self.leave()
            return self.count(Unary(t.pos, operand.end, "not", operand))
        return self.parse_cmp()

    def parse_cmp(self) -> Node:
        left = self.parse_add()
        if self.tok.kind == "OP" and self.tok.text in CMP_OPS:
            op = self.advance().text
            right = self.parse_add()
            if self.tok.kind == "OP" and self.tok.text in CMP_OPS:
                raise self.error("chained comparisons are not allowed (use 'and')")
            return self.count(Binary(left.pos, right.end, op, left, right))
        return left

    def parse_add(self) -> Node:
        left = self.parse_mul()
        while self.is_op("+", "-"):
            op = self.advance().text
            right = self.parse_mul()
            left = self.count(Binary(left.pos, right.end, op, left, right))
        return left

    def parse_mul(self) -> Node:
        left = self.parse_unary()
        while self.is_op("*", "/", "%"):
            op = self.advance().text
            right = self.parse_unary()
            left = self.count(Binary(left.pos, right.end, op, left, right))
        return left

    def parse_unary(self) -> Node:
        if self.is_op("-", "+"):
            t = self.advance()
            self.enter()
            operand = self.parse_unary()
            self.leave()
            return self.count(Unary(t.pos, operand.end, t.text, operand))
        return self.parse_primary()

    def parse_primary(self) -> Node:
        t = self.tok
        if t.kind == "NUM":
            self.advance()
            return self.count(Num(t.pos, t.end, Decimal(t.text)))
        if t.kind == "STR":
            self.advance()
            return self.count(Str(t.pos, t.end, str(t.value)))
        if t.kind == "KW" and t.text in ("true", "false"):
            self.advance()
            return self.count(Bool(t.pos, t.end, t.text == "true"))
        if t.kind == "IDENT":
            self.advance()
            if self.is_op("("):
                return self.parse_call(t)
            return self.count(Ident(t.pos, t.end, t.text))
        if self.is_op("("):
            self.advance()
            self.enter()
            inner = self.parse_or()
            self.leave()
            self.expect_op(")")
            return inner
        if t.kind == "EOF":
            raise self.error("unexpected end of expression")
        raise self.error(f"unexpected '{t.text}'")

    def parse_call(self, name_tok: Token) -> Node:
        name = name_tok.text.lower()
        if name not in FUNCTIONS:
            raise FormulaUnknownIdentifier(
                f"unknown function '{name_tok.text}'", position=name_tok.pos, source=self.source
            )
        self.expect_op("(")
        self.enter()
        args: list[Node] = []
        if not self.is_op(")"):
            while True:
                args.append(self.parse_or())
                if len(args) > MAX_CALL_ARGS:
                    raise self.error(f"more than {MAX_CALL_ARGS} arguments")
                if self.is_op(","):
                    self.advance()
                    continue
                break
        self.leave()
        close = self.expect_op(")")
        if self.is_op("("):
            raise self.error("calling the result of a call is not allowed")
        return self.count(Call(name_tok.pos, close.end, name, tuple(args)))


def parse(source: str, limits: Limits = DEFAULT_LIMITS) -> tuple[Node, int]:
    """Parse ``source``; returns (root node, node count)."""
    p = _Parser(source, limits)
    root = p.parse()
    return root, p.nodes
