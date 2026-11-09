"""Tokenizer of the formula DSL.

Only ASCII is accepted outside string literals (no homoglyphs, no full-width
digits, no zero-width or bidi characters). Strings reject control and format
characters. Exponent notation, ``**``, ``.``, ``[]``, ``{}``, ``;`` and ``=``
assignment are syntax errors with a position.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from decimal import Decimal

from workflow.formula.errors import FormulaBudgetExceeded, FormulaSyntaxError
from workflow.formula.limits import DEFAULT_LIMITS, MAX_LITERAL_DIGITS, MAX_STRING_CHARS, Limits

KEYWORDS = frozenset({"and", "or", "not", "true", "false"})

#: Words that must never appear as identifiers (Python / injection vocabulary).
RESERVED = frozenset(
    {
        "lambda", "import", "from", "for", "while", "in", "is", "def", "class", "return", "yield", "async",
        "await", "with", "del", "global", "nonlocal", "try", "except", "raise", "assert", "pass", "break",
        "continue", "elif", "else", "exec", "eval", "compile", "open", "globals", "locals", "vars", "getattr",
        "setattr", "delattr", "hasattr", "type", "object", "builtins", "input", "print", "help", "dir", "id",
        "memoryview", "bytes", "bytearray", "breakpoint", "exit", "quit", "os", "sys", "subprocess", "self",
        "none", "null", "select", "insert", "update", "delete", "drop", "union",
    }
)  # fmt: skip

MAX_IDENT_CHARS = 64

TWO_CHAR_OPS = frozenset({"==", "!=", "<=", ">="})
ONE_CHAR_OPS = frozenset({"+", "-", "*", "/", "%", "<", ">", "(", ")", ","})

_EXPLAIN = {
    ".": "attribute access is not allowed",
    "[": "indexing is not allowed (use lookup)",
    "]": "indexing is not allowed (use lookup)",
    "{": "braces are not allowed",
    "}": "braces are not allowed",
    ";": "statements are not allowed",
    ":": "':' is not allowed (no lambdas, slices or annotations)",
    "=": "assignment is not allowed (use == to compare)",
    "@": "decorators/matrix operators are not allowed",
    "^": "'^' is not allowed (no power operator)",
    "&": "use 'and'",
    "|": "use 'or'",
    "~": "'~' is not allowed",
    "!": "use 'not' or '!='",
    "#": "comments are not allowed",
    "\\": "backslashes are only allowed inside strings",
    "`": "backticks are not allowed",
    "$": "'$' is not allowed",
    "?": "'?' is not allowed (use if(cond, a, b))",
}


@dataclass(frozen=True, slots=True)
class Token:
    kind: str  # NUM STR IDENT KW OP EOF
    text: str
    pos: int
    end: int
    value: Decimal | str | None = None


def _is_ascii_letter(ch: str) -> bool:
    return ("a" <= ch <= "z") or ("A" <= ch <= "Z")


def _is_digit(ch: str) -> bool:
    return "0" <= ch <= "9"


def tokenize(source: str, limits: Limits = DEFAULT_LIMITS) -> list[Token]:
    if not isinstance(source, str):
        raise FormulaSyntaxError("the expression must be text")
    if len(source) > limits.max_expression_chars:
        raise FormulaBudgetExceeded(
            f"expression longer than {limits.max_expression_chars} characters", budget="expression_chars"
        )
    tokens: list[Token] = []
    i, n = 0, len(source)

    def err(msg: str, at: int) -> FormulaSyntaxError:
        return FormulaSyntaxError(msg, position=at, source=source)

    while i < n:
        ch = source[i]
        if ch in " \t\r\n":
            i += 1
            continue
        if _is_digit(ch):
            start = i
            while i < n and _is_digit(source[i]):
                i += 1
            if i < n and source[i] == "." and i + 1 < n and _is_digit(source[i + 1]):
                i += 1
                while i < n and _is_digit(source[i]):
                    i += 1
            if i < n and (source[i] == "." or source[i] == "_" or _is_ascii_letter(source[i])):
                what = "exponent notation is not allowed" if source[i] in "eE" else "malformed number"
                raise err(what, i)
            text = source[start:i]
            if len(text.replace(".", "")) > MAX_LITERAL_DIGITS:
                raise FormulaBudgetExceeded(f"number literal longer than {MAX_LITERAL_DIGITS} digits", budget="literal")
            tokens.append(Token("NUM", text, start, i, Decimal(text)))
            continue
        if _is_ascii_letter(ch) or ch == "_":
            start = i
            while i < n and (_is_ascii_letter(source[i]) or _is_digit(source[i]) or source[i] == "_"):
                i += 1
            if i < n and ord(source[i]) > 127 and (source[i].isalnum() or unicodedata.category(source[i]) == "Mn"):
                raise err("non-ASCII characters are not allowed in names", i)
            text = source[start:i]
            if text.startswith("_") or "__" in text:
                raise err("names starting with '_' or containing '__' are not allowed", start)
            if len(text) > MAX_IDENT_CHARS:
                raise err(f"name longer than {MAX_IDENT_CHARS} characters", start)
            low = text.lower()
            if low in KEYWORDS:
                tokens.append(Token("KW", low, start, i))
            elif low in RESERVED:
                raise err(f"'{text}' is a reserved word", start)
            else:
                tokens.append(Token("IDENT", text, start, i))
            continue
        if ch in "'\"":
            start, quote = i, ch
            i += 1
            buf: list[str] = []
            while True:
                if i >= n:
                    raise err("unterminated string", start)
                c = source[i]
                if c == quote:
                    i += 1
                    break
                if c == "\\":
                    if i + 1 >= n:
                        raise err("unterminated string", start)
                    esc = source[i + 1]
                    mapped = {"\\": "\\", "'": "'", '"': '"', "n": "\n", "t": "\t"}.get(esc)
                    if mapped is None:
                        raise err("unsupported escape sequence", i)
                    buf.append(mapped)
                    i += 2
                else:
                    cat = unicodedata.category(c)
                    if cat in ("Cc", "Cf", "Cs", "Co", "Cn") or (cat.startswith("Z") and c != " "):
                        raise err(f"character U+{ord(c):04X} is not allowed in strings", i)
                    buf.append(c)
                    i += 1
                if len(buf) > MAX_STRING_CHARS:
                    raise FormulaBudgetExceeded(f"string longer than {MAX_STRING_CHARS} characters", budget="string")
            text = "".join(buf)
            tokens.append(Token("STR", source[start:i], start, i, text))
            continue
        two = source[i : i + 2]
        if two == "**":
            raise err("the power operator is not allowed", i)
        if two == "//":
            raise err("floor division is not allowed (use floor(a / b))", i)
        if two in TWO_CHAR_OPS:
            tokens.append(Token("OP", two, i, i + 2))
            i += 2
            continue
        if two == "<>":
            tokens.append(Token("OP", "!=", i, i + 2))
            i += 2
            continue
        if ch in ONE_CHAR_OPS:
            tokens.append(Token("OP", ch, i, i + 1))
            i += 1
            continue
        if ord(ch) > 127:
            raise err(f"character U+{ord(ch):04X} is not allowed outside strings (ASCII only)", i)
        raise err(_EXPLAIN.get(ch, f"unexpected character {ch!r}"), i)
    tokens.append(Token("EOF", "", n, n))
    return tokens
