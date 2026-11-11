"""Sandboxed document template language (P17 "templates only access a whitelisted context").

Syntax (a strict, Jinja-like subset — there is no Python evaluation at all):

* ``{{ path | filter | filter:arg }}`` — ``path`` is ``name(.name|.index)*`` looked up **only** in plain JSON data
  (dict keys and list indices of the render context). Names match ``[A-Za-z][A-Za-z0-9_]*`` and never contain ``__``
  or start with ``_``; there is no attribute access, no call, no operator, no literal outside filter arguments, so the
  classic SSTI escapes (``''.__class__``, ``cycler.__init__.__globals__``, ``config``, ``self``) fail at **parse** time
  with :class:`TemplateError` (``template_render_failed``).
* ``{% for x in path %} … {% endfor %}``, ``{% if path %} … {% else %} … {% endif %}`` (truthiness only).
* Filters (whitelist): ``upper lower money number words date default truncate join length``.
* Limits (D-008 spirit): body ≤ 10,000 characters, nesting ≤ 16, ≤ 5,000 loop iterations, output ≤ 200,000 characters.

Interpolated values are marked with :data:`VALUE_MARK` so that data can never inject layout markup (``#``, ``|``,
``@``…) — :mod:`documents.layout` treats only template-authored line prefixes as markup.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from documents.words import amount_in_words

MAX_BODY = 10_000
MAX_DEPTH = 16
MAX_ITERATIONS = 5_000
MAX_OUTPUT = 200_000
VALUE_MARK = "\x01"

_TOKEN = re.compile(r"(\{\{.*?\}\}|\{%.*?%\})", re.DOTALL)
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")
_INDEX = re.compile(r"\d{1,4}\Z")
_STRING = re.compile(r"'([^'\\{}%]*)'\Z|\"([^\"\\{}%]*)\"\Z")
_INT = re.compile(r"-?\d{1,6}\Z")


class TemplateError(ValueError):
    """Invalid or unsafe template, or a failure while rendering it (→ ``template_render_failed``)."""

    def __init__(self, message: str, *, line: int | None = None) -> None:
        super().__init__(message if line is None else f"line {line}: {message}")
        self.line = line


# --------------------------------------------------------------------------- AST


@dataclass(frozen=True, slots=True)
class Path:
    parts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Filter:
    name: str
    arg: Path | str | int | None = None


@dataclass(frozen=True, slots=True)
class Expr:
    path: Path
    filters: tuple[Filter, ...]
    line: int


@dataclass(slots=True)
class Text:
    value: str


@dataclass(slots=True)
class For:
    var: str
    source: Path
    line: int
    body: list[Any] = field(default_factory=list)


@dataclass(slots=True)
class If:
    cond: Path
    line: int
    then: list[Any] = field(default_factory=list)
    other: list[Any] = field(default_factory=list)
    in_else: bool = False


def _check_name(name: str, line: int) -> str:
    if "__" in name or name.startswith("_") or not _NAME.match(name):
        raise TemplateError(f"forbidden name {name!r} (only plain data names are allowed)", line=line)
    return name


def parse_path(text: str, line: int) -> Path:
    raw = text.strip()
    if not raw:
        raise TemplateError("empty expression", line=line)
    parts = raw.split(".")
    out: list[str] = [_check_name(parts[0], line)]
    for p in parts[1:]:
        if _INDEX.match(p):
            out.append(p)
        else:
            out.append(_check_name(p, line))
    return Path(tuple(out))


def _parse_arg(raw: str, line: int) -> Path | str | int:
    raw = raw.strip()
    m = _STRING.match(raw)
    if m:
        return m.group(1) if m.group(1) is not None else m.group(2)
    if _INT.match(raw):
        return int(raw)
    return parse_path(raw, line)


def _parse_expr(inner: str, line: int) -> Expr:
    pieces = inner.split("|")
    path = parse_path(pieces[0], line)
    filters: list[Filter] = []
    for piece in pieces[1:]:
        name, sep, arg = piece.partition(":")
        name = name.strip()
        if name not in FILTERS:
            raise TemplateError(f"unknown filter {name!r}", line=line)
        filters.append(Filter(name, _parse_arg(arg, line) if sep else None))
    return Expr(path, tuple(filters), line)


def parse(body: str) -> list[Any]:
    if not isinstance(body, str):
        raise TemplateError("a template body is text")
    if len(body) > MAX_BODY:
        raise TemplateError(f"a template body has at most {MAX_BODY} characters")
    root: list[Any] = []
    stack: list[Any] = []

    def target() -> list[Any]:
        if not stack:
            return root
        top = stack[-1]
        if isinstance(top, For):
            return top.body
        return top.other if top.in_else else top.then

    pos = 0
    for m in _TOKEN.finditer(body):
        line = body.count("\n", 0, m.start()) + 1
        if m.start() > pos:
            target().append(Text(body[pos : m.start()]))
        pos = m.end()
        token = m.group(0)
        inner = token[2:-2].strip()
        if token.startswith("{{"):
            target().append(_parse_expr(inner, line))
            continue
        words = inner.split()
        if not words:
            raise TemplateError("empty block tag", line=line)
        tag = words[0]
        if tag == "for":
            if len(words) != 4 or words[2] != "in":
                raise TemplateError("use {% for name in path %}", line=line)
            node: Any = For(_check_name(words[1], line), parse_path(words[3], line), line)
            target().append(node)
            stack.append(node)
        elif tag == "if":
            if len(words) != 2:
                raise TemplateError("use {% if path %} (no operators)", line=line)
            node = If(parse_path(words[1], line), line)
            target().append(node)
            stack.append(node)
        elif tag == "else":
            if not stack or not isinstance(stack[-1], If) or stack[-1].in_else or len(words) != 1:
                raise TemplateError("{% else %} outside {% if %}", line=line)
            stack[-1].in_else = True
        elif tag in ("endfor", "endif"):
            expected = For if tag == "endfor" else If
            if not stack or not isinstance(stack[-1], expected) or len(words) != 1:
                raise TemplateError(f"unexpected {{% {tag} %}}", line=line)
            stack.pop()
        else:
            raise TemplateError(f"unknown tag {tag!r}", line=line)
        if len(stack) > MAX_DEPTH:
            raise TemplateError(f"nesting deeper than {MAX_DEPTH}", line=line)
    if stack:
        raise TemplateError(f"unclosed {{% {'for' if isinstance(stack[-1], For) else 'if'} %}}", line=stack[-1].line)
    if "{{" in body[pos:] or "{%" in body[pos:]:
        raise TemplateError("unterminated tag")
    root.append(Text(body[pos:]))
    return root


# --------------------------------------------------------------------------- evaluation

_MISSING = object()
_PLAIN = (str, int, Decimal, bool, type(None), dt.date)


def lookup(scope: Sequence[Mapping[str, Any]], path: Path) -> Any:
    value: Any = _MISSING
    for frame in reversed(scope):
        if path.parts[0] in frame:
            value = frame[path.parts[0]]
            break
    if value is _MISSING:
        return None
    for part in path.parts[1:]:
        if isinstance(value, Mapping):
            value = value.get(part)
        elif isinstance(value, list | tuple) and part.isdigit():
            idx = int(part)
            value = value[idx] if idx < len(value) else None
        else:
            return None  # never getattr: a path only walks plain data
        if value is None:
            return None
    if not isinstance(value, _PLAIN + (Mapping, list, tuple)):
        raise TemplateError(f"value of {'.'.join(path.parts)} is not plain data")
    return value


def to_decimal(value: Any) -> Decimal | None:
    if isinstance(value, Mapping) and "amount" in value:
        value = value["amount"]
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def format_number(value: Decimal, places: int = 2) -> str:
    """es-CO grouping: thousands '.', decimals ','."""
    q = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    sign = "-" if q < 0 else ""
    text = format(abs(q), "f")
    whole, _, frac = text.partition(".")
    groups: list[str] = []
    while whole:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    out = ".".join(groups) or "0"
    return f"{sign}{out},{frac}" if frac else f"{sign}{out}"


def _currency_of(value: Any, arg: Any) -> str:
    if isinstance(arg, str) and arg:
        return arg
    if isinstance(value, Mapping) and isinstance(value.get("currency"), str):
        return str(value["currency"])
    return "COP"


def _f_money(value: Any, arg: Any) -> str:
    d = to_decimal(value)
    if d is None:
        return ""
    cur = _currency_of(value, arg)
    return f"$ {format_number(d)}" if cur == "COP" else f"{cur} {format_number(d)}"


def _f_number(value: Any, arg: Any) -> str:
    d = to_decimal(value)
    if d is None:
        return ""
    places = arg if isinstance(arg, int) and 0 <= arg <= 10 else 2
    return format_number(d, places)


def _f_words(value: Any, arg: Any) -> str:
    d = to_decimal(value)
    return "" if d is None else amount_in_words(d, _currency_of(value, arg))


def _f_date(value: Any, arg: Any) -> str:
    if isinstance(value, dt.date):
        day = value
    else:
        try:
            day = dt.date.fromisoformat(str(value)[:10])
        except ValueError:
            return str(value)
    return day.strftime("%d/%m/%Y")


def _f_default(value: Any, arg: Any) -> Any:
    return value if value not in (None, "", [], {}) else ("" if arg is None else arg)


def _f_truncate(value: Any, arg: Any) -> str:
    text = _stringify(value)
    limit = arg if isinstance(arg, int) and arg > 0 else 60
    return text if len(text) <= limit else text[: max(1, limit - 1)] + "…"


def _f_join(value: Any, arg: Any) -> str:
    sep = arg if isinstance(arg, str) else ", "
    if isinstance(value, list | tuple):
        return sep.join(_stringify(v) for v in value)
    return _stringify(value)


def _f_length(value: Any, arg: Any) -> int:
    return len(value) if isinstance(value, list | tuple | Mapping | str) else 0


FILTERS: dict[str, Callable[[Any, Any], Any]] = {
    "upper": lambda v, a: _stringify(v).upper(),
    "lower": lambda v, a: _stringify(v).lower(),
    "money": _f_money,
    "number": _f_number,
    "words": _f_words,
    "date": _f_date,
    "default": _f_default,
    "truncate": _f_truncate,
    "join": _f_join,
    "length": _f_length,
}


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Sí" if value else "No"
    if isinstance(value, Mapping):
        if "amount" in value:
            return _f_money(value, None)
        return ""
    if isinstance(value, list | tuple):
        return ", ".join(_stringify(v) for v in value)
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value)


def _clean(text: str) -> str:
    # data never carries layout markup or tags: '|' would split table cells, newlines would start new lines
    return text.replace("|", "¦").replace("\r", " ").replace("\n", " ").replace(VALUE_MARK, "")


@dataclass
class _Budget:
    iterations: int = 0
    size: int = 0


def _eval(nodes: list[Any], scope: list[dict[str, Any]], out: list[str], budget: _Budget) -> None:
    for node in nodes:
        if isinstance(node, Text):
            out.append(node.value)
            budget.size += len(node.value)
        elif isinstance(node, Expr):
            value: Any = lookup(scope, node.path)
            for f in node.filters:
                arg = lookup(scope, f.arg) if isinstance(f.arg, Path) else f.arg
                try:
                    value = FILTERS[f.name](value, arg)
                except (ValueError, TypeError, ArithmeticError) as exc:
                    raise TemplateError(f"filter {f.name} failed: {exc}", line=node.line) from exc
            text = VALUE_MARK + _clean(_stringify(value))
            out.append(text)
            budget.size += len(text)
        elif isinstance(node, For):
            items = lookup(scope, node.source)
            if items is None:
                continue
            if not isinstance(items, list | tuple):
                raise TemplateError(f"{'.'.join(node.source.parts)} is not a list", line=node.line)
            for n, item in enumerate(items):
                budget.iterations += 1
                if budget.iterations > MAX_ITERATIONS:
                    raise TemplateError(f"more than {MAX_ITERATIONS} loop iterations", line=node.line)
                scope.append({node.var: item, "loop": {"index": n + 1, "first": n == 0, "last": n == len(items) - 1}})
                try:
                    _eval(node.body, scope, out, budget)
                finally:
                    scope.pop()
        elif isinstance(node, If):
            _eval(node.then if lookup(scope, node.cond) else node.other, scope, out, budget)
        if budget.size > MAX_OUTPUT:
            raise TemplateError(f"output larger than {MAX_OUTPUT} characters")


def render(body: str, context: Mapping[str, Any]) -> str:
    """Render ``body`` against plain JSON ``context`` (dicts/lists/str/numbers/Decimal/bool/None/date)."""
    nodes = parse(body)
    out: list[str] = []
    _eval(nodes, [dict(context)], out, _Budget())
    return "".join(out)
