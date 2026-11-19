"""Compile a Dataset row filter to a small, parameterized predicate language."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from agentlens.infra.signoz.dialect import (
    ATTRIBUTES_PREFIX,
    RESOURCE_ATTRIBUTES_PREFIX,
    ColumnDialect,
    FlatColumnDialect,
    quote_identifier,
)

_ERROR = "Unsupported Dataset query filter"
_MAX_DEPTH = 64
_MAX_NUMBER_LENGTH = 128
_TABLE_PART = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_TOKEN = re.compile(
    r"\s*(?:(?P<identifier>`(?:``|[^`])+`)"
    r"|(?P<string>'(?:''|\\.|[^'\\])*'|\"(?:\"\"|\\.|[^\"\\])*\")"
    r"|(?P<number>[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)"
    r"|(?P<operator><=|>=|<>|!=|=|<|>)"
    r"|(?P<word>[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*){0,2})"
    r"|(?P<delimiter>[(),]))",
    re.ASCII,
)
_WORDS = {"AND", "OR", "NOT", "IN", "IS", "NULL", "TRUE", "FALSE"}
_OPERATORS = {symbol: symbol for symbol in ("=", "!=", "<>", "<", "<=", ">", ">=")}
_STRING_ESCAPES = {
    "0": "\0",
    "b": "\b",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "Z": "\x1a",
    "%": r"\%",
    "_": r"\_",
}


def _string_value(token: str) -> str:
    quote = token[0]
    pattern = r"\\(.)|''" if quote == "'" else r'\\(.)|""'
    return re.sub(
        pattern,
        lambda match: _STRING_ESCAPES.get(match[1], match[1]) if match[1] else quote,
        token[1:-1],
        flags=re.DOTALL,
    )


class _Parser:
    def __init__(self, source: str, dialect: ColumnDialect, parameters: dict[str, Any]) -> None:
        self.source = source
        self.dialect = dialect
        self.parameters = parameters
        self.position = 0
        self.token: tuple[str, str] | None = self._read()

    def _read(self) -> tuple[str, str] | None:
        if self.position == len(self.source):
            return None
        match = _TOKEN.match(self.source, self.position)
        if not match or not match.lastgroup:
            raise ValueError(_ERROR)
        self.position = match.end()
        return match.lastgroup, match.group(match.lastgroup)

    def _advance(self) -> tuple[str, str]:
        if not self.token:
            raise ValueError(_ERROR)
        token = self.token
        self.token = self._read()
        return token

    def _accept(self, kind: str, value: str) -> bool:
        if self.token and self.token[0] == kind and self.token[1].upper() == value:
            self._advance()
            return True
        return False

    def _expect(self, kind: str, value: str) -> None:
        if not self._accept(kind, value):
            raise ValueError(_ERROR)

    def _literal(self) -> str:
        kind, value = self._advance()
        if kind == "string":
            bound = _string_value(value)
        elif kind == "number":
            # Bound the plain representation used by text-based SQL drivers.
            if len(value) > _MAX_NUMBER_LENGTH:
                raise ValueError(_ERROR)
            _, scientific, exponent = value.lower().partition("e")
            try:
                if scientific and not -324 <= int(exponent) <= 308:
                    raise ValueError(_ERROR)
                bound = Decimal(value) if "." in value or scientific else int(value)
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(_ERROR) from exc
        elif kind == "word" and value.upper() in {"NULL", "TRUE", "FALSE"}:
            bound = {"NULL": None, "TRUE": True, "FALSE": False}[value.upper()]
        else:
            raise ValueError(_ERROR)
        parameter = f"filter_{len(self.parameters)}"
        self.parameters[parameter] = bound
        return f":{parameter}"

    def _column(self) -> str:
        kind, name = self._advance()
        if kind == "word" and name.upper() not in _WORDS:
            # Legacy bare names are physical; backticks mark a logical column.
            return ".".join(quote_identifier(part) for part in name.split("."))
        if kind == "identifier":
            logical = name[1:-1].replace("``", "`")
            if not logical or any(ord(char) < 32 for char in logical):
                raise ValueError(_ERROR)
            # Production JSON columns bind the path, including colons in keys.
            if ":" in logical and not logical.startswith(
                (ATTRIBUTES_PREFIX, RESOURCE_ATTRIBUTES_PREFIX)
            ):
                raise ValueError(_ERROR)
            return self.dialect.col(logical, parameters=self.parameters)
        raise ValueError(_ERROR)

    def _comparison(self) -> str:
        column = self._column()
        if self._accept("word", "IS"):
            suffix = "IS NOT NULL" if self._accept("word", "NOT") else "IS NULL"
            self._expect("word", "NULL")
            return f"{column} {suffix}"
        negate = self._accept("word", "NOT")
        if negate or self._accept("word", "IN"):
            if negate:
                self._expect("word", "IN")
            self._expect("delimiter", "(")
            values = [self._literal()]
            while self._accept("delimiter", ","):
                values.append(self._literal())
            self._expect("delimiter", ")")
            operator = "NOT IN" if negate else "IN"
            return f"{column} {operator} ({', '.join(values)})"
        kind, operator = self._advance()
        if kind != "operator":
            raise ValueError(_ERROR)
        return f"{column} {_OPERATORS[operator]} {self._literal()}"

    def _primary(self, depth: int) -> str:
        if depth > _MAX_DEPTH:
            raise ValueError(_ERROR)
        if self._accept("word", "NOT"):
            return f"NOT ({self._primary(depth + 1)})"
        if self._accept("delimiter", "("):
            expression = self._expression(depth + 1)
            self._expect("delimiter", ")")
            return f"({expression})"
        return self._comparison()

    def _conjunction(self, depth: int) -> str:
        clauses = [self._primary(depth)]
        while self._accept("word", "AND"):
            clauses.append(self._primary(depth))
        return " AND ".join(clauses)

    def _expression(self, depth: int) -> str:
        clauses = [self._conjunction(depth)]
        while self._accept("word", "OR"):
            clauses.append(self._conjunction(depth))
        return " OR ".join(clauses)

    def render(self) -> str:
        expression = self._expression(0)
        if self.token:
            raise ValueError(_ERROR)
        return expression


def compile_dataset_filter(
    where: str | None, dialect: ColumnDialect, parameters: dict[str, Any]
) -> str:
    if where is None:
        return ""
    if not isinstance(where, str):
        raise ValueError(_ERROR)
    where = where.strip()
    return _Parser(where, dialect, parameters).render() if where else ""


def normalize_dataset_table(value: object) -> str:
    """Accept one or two simple identifier parts and remove SQL decoration."""
    message = "Invalid Dataset query table"
    if not isinstance(value, str):
        raise ValueError(message)
    parts = value.strip().split(".")
    if len(parts) not in {1, 2}:
        raise ValueError(message)
    normalized = []
    for part in parts:
        part = part.strip()
        if part.startswith("`") and part.endswith("`"):
            part = part[1:-1]
        if not _TABLE_PART.fullmatch(part):
            raise ValueError(message)
        normalized.append(part)
    return ".".join(normalized)


def validate_dataset_filter(where: str | None) -> None:
    """Validate the Dataset filter before storage using its read-side grammar."""
    compile_dataset_filter(where, FlatColumnDialect(), {})
