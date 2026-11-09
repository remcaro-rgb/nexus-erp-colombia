"""AST of the formula DSL. Built by :mod:`workflow.formula.parser`; never by Python's ``ast``."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Node:
    pos: int
    end: int


@dataclass(frozen=True, slots=True)
class Num(Node):
    value: Decimal


@dataclass(frozen=True, slots=True)
class Str(Node):
    value: str


@dataclass(frozen=True, slots=True)
class Bool(Node):
    value: bool


@dataclass(frozen=True, slots=True)
class Ident(Node):
    name: str


@dataclass(frozen=True, slots=True)
class Unary(Node):
    op: str  # "-", "+", "not"
    operand: Node


@dataclass(frozen=True, slots=True)
class Binary(Node):
    op: str  # + - * / % == != < <= > >= and or
    left: Node
    right: Node


@dataclass(frozen=True, slots=True)
class Call(Node):
    name: str
    args: tuple[Node, ...]


Value = Decimal | str | bool | dt.date


def walk(node: Node) -> list[Node]:
    """All nodes, pre-order, iteratively (no recursion on untrusted depth)."""
    out: list[Node] = []
    stack: list[Node] = [node]
    while stack:
        n = stack.pop()
        out.append(n)
        if isinstance(n, Unary):
            stack.append(n.operand)
        elif isinstance(n, Binary):
            stack.extend((n.right, n.left))
        elif isinstance(n, Call):
            stack.extend(reversed(n.args))
    return out
